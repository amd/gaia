# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Medical Intake Agent for processing patient intake forms.

Watches a directory for new intake forms (images/PDFs), extracts patient
data using VLM, and stores records in a SQLite database.

NOTE: This is a demonstration/proof-of-concept application.
Not intended for production use with real patient data.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base import Agent
from gaia.agents.base.tools import tool
from gaia.database import DatabaseMixin
from gaia.llm.vlm_client import detect_image_mime_type
from gaia.utils import (
    FileWatcherMixin,
    compute_file_hash,
    detect_field_changes,
    extract_json_from_text,
    pdf_page_to_image,
)

from .constants import (
    EXTRACTION_PROMPT,
    PATIENT_SCHEMA,
    STANDARD_COLUMNS,
    UPDATABLE_COLUMNS,
    estimate_manual_entry_time,
)

logger = logging.getLogger(__name__)


class MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin):
    """
    Agent for processing medical intake forms automatically.

    Watches a directory for new intake forms (images/PDFs), extracts
    patient data using VLM (Vision Language Model), and stores the
    records in a SQLite database.

    Features:
    - Automatic file watching for new intake forms
    - VLM-powered data extraction from images
    - SQLite database storage with full-text search
    - Tools for patient lookup and management
    - Rich console output for processing status

    Example:
        from gaia.agents.emr import MedicalIntakeAgent

        agent = MedicalIntakeAgent(
            watch_dir="./intake_forms",
            db_path="./data/patients.db",
        )

        # Agent automatically processes new files in watch_dir
        # Query the agent about patients
        agent.process_query("How many patients were processed today?")
        agent.process_query("Find patient John Smith")

        # Cleanup
        agent.stop()
    """

    def __init__(
        self,
        watch_dir: str = "./intake_forms",
        db_path: str = "./data/patients.db",
        vlm_model: str = "Qwen3-VL-4B-Instruct-GGUF",
        auto_start_watching: bool = True,
        **kwargs,
    ):
        """
        Initialize the Medical Intake Agent.

        Args:
            watch_dir: Directory to watch for new intake forms
            db_path: Path to SQLite database for patient records
            vlm_model: VLM model to use for extraction
            auto_start_watching: Start watching immediately (default: True)
            **kwargs: Additional arguments for Agent base class
        """
        # Set attributes before super().__init__() as it may call _get_system_prompt()
        self._watch_dir = Path(watch_dir)
        self._db_path = db_path
        self._vlm_model = vlm_model
        self._vlm = None
        self._processed_files: List[Dict[str, Any]] = []
        self._auto_start_watching = auto_start_watching

        # Statistics
        self._stats = {
            "files_processed": 0,
            "extraction_success": 0,
            "extraction_failed": 0,
            "new_patients": 0,
            "returning_patients": 0,
            "total_processing_time_seconds": 0.0,
            "total_estimated_manual_seconds": 0.0,
            "start_time": time.time(),
        }

        # Progress callback for external monitoring (e.g., dashboard SSE)
        # Signature: callback(filename, step_num, total_steps, step_name, status)
        self._progress_callback: Optional[callable] = None

        # Set reasonable defaults for agent - higher max_steps for interactive use
        kwargs.setdefault("max_steps", 50)

        super().__init__(**kwargs)

        # Initialize database
        self._init_database()

        # Load historical stats from database (for pre-processed forms)
        self._load_historical_stats()

        # Create watch directory if needed
        self._watch_dir.mkdir(parents=True, exist_ok=True)

        # Start file watching if requested
        if auto_start_watching:
            self._start_file_watching()

    def _init_database(self) -> None:
        """Initialize the patient database."""
        try:
            # Ensure data directory exists
            db_dir = Path(self._db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            # Initialize database with schema
            self.init_db(self._db_path)
            self.execute(PATIENT_SCHEMA)
            logger.info(f"Database initialized: {self._db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _load_historical_stats(self) -> None:
        """Load historical processing stats from database for pre-processed forms.

        This ensures efficiency metrics include forms processed in previous sessions,
        not just the current agent instance.
        """
        try:
            # Get aggregate stats from patients table
            result = self.query("""
                SELECT
                    COUNT(*) as total_patients,
                    COALESCE(SUM(processing_time_seconds), 0) as total_processing_time,
                    COALESCE(SUM(estimated_manual_seconds), 0) as total_estimated_manual,
                    SUM(CASE WHEN is_new_patient = 1 THEN 1 ELSE 0 END) as new_patients,
                    SUM(CASE WHEN is_new_patient = 0 THEN 1 ELSE 0 END) as returning_patients
                FROM patients
                """)

            if result and result[0]:
                stats = result[0]
                self._stats["extraction_success"] = stats.get("total_patients", 0) or 0
                self._stats["files_processed"] = stats.get("total_patients", 0) or 0
                self._stats["total_processing_time_seconds"] = float(
                    stats.get("total_processing_time", 0) or 0
                )
                self._stats["total_estimated_manual_seconds"] = float(
                    stats.get("total_estimated_manual", 0) or 0
                )
                self._stats["new_patients"] = stats.get("new_patients", 0) or 0
                self._stats["returning_patients"] = (
                    stats.get("returning_patients", 0) or 0
                )

                if self._stats["extraction_success"] > 0:
                    logger.info(
                        f"Loaded historical stats: {self._stats['extraction_success']} forms, "
                        f"{self._stats['total_processing_time_seconds']:.1f}s AI time, "
                        f"{self._stats['total_estimated_manual_seconds']:.1f}s manual time"
                    )
        except Exception as e:
            # Don't fail if historical stats can't be loaded (e.g., schema mismatch)
            logger.warning(f"Could not load historical stats: {e}")

    def _start_file_watching(self) -> None:
        """Start watching the intake directory for new files."""
        # First, process any existing files (works even if watcher fails)
        self._process_existing_files()

        # Then set up the watcher for new files
        try:
            self.watch_directory(
                self._watch_dir,
                on_created=self._on_file_created,
                on_modified=self._on_file_modified,
                extensions=[".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp"],
                debounce_seconds=2.0,
            )
            logger.info(f"Watching for intake forms: {self._watch_dir}")
        except Exception as e:
            logger.warning(f"File watching not available: {e}")

    def _print_file_listing(
        self, files: list, processed_hashes: set
    ) -> tuple[int, int]:
        """Print a styled listing of files in the watch directory.

        Returns:
            Tuple of (new_count, processed_count)
        """
        from rich.console import Console
        from rich.table import Table

        console = Console()

        table = Table(
            title=f"📁 {self._watch_dir}", show_header=True, header_style="bold cyan"
        )
        table.add_column("File", style="white")
        table.add_column("Size", justify="right", style="dim")
        table.add_column("Hash", style="dim")
        table.add_column("Status", justify="center")

        new_count = 0
        processed_count = 0

        for f in sorted(files):
            try:
                size = f.stat().st_size
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"
            except OSError:
                size_str = "?"

            # Compute hash for status check
            file_hash = compute_file_hash(f)
            hash_display = file_hash[:8] + "..." if file_hash else "?"

            if file_hash and file_hash in processed_hashes:
                status = "[dim]✓ processed[/dim]"
                processed_count += 1
            else:
                status = "[green]● new[/green]"
                new_count += 1

            table.add_row(f.name, size_str, hash_display, status)

        console.print(table)

        # Print summary
        summary_parts = []
        if new_count > 0:
            summary_parts.append(f"[green]{new_count} new[/green]")
        if processed_count > 0:
            summary_parts.append(f"[dim]{processed_count} already processed[/dim]")
        if summary_parts:
            console.print(f"  {', '.join(summary_parts)}")
        console.print()

        return new_count, processed_count

    def _process_existing_files(self) -> None:
        """Scan and process any existing files in the watch directory."""
        supported_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp"}

        # Check directory exists
        if not self._watch_dir.exists():
            self.console.print_warning(
                f"Watch directory does not exist: {self._watch_dir}"
            )
            return

        # Use case-insensitive matching on Windows
        existing_files = set()
        try:
            for f in self._watch_dir.iterdir():
                if f.is_file() and f.suffix.lower() in supported_extensions:
                    existing_files.add(f.absolute())
        except Exception as e:
            self.console.print_error(f"Could not scan directory: {e}")
            return

        # Get all processed file hashes from database
        processed_hashes = set()
        try:
            results = self.query(
                "SELECT DISTINCT file_hash FROM patients WHERE file_hash IS NOT NULL"
            )
            for r in results:
                if r.get("file_hash"):
                    processed_hashes.add(r["file_hash"])
        except Exception as e:
            logger.debug(f"Could not query processed hashes: {e}")

        # Always show file listing at startup
        if existing_files:
            new_count, _processed_count = self._print_file_listing(
                existing_files, processed_hashes
            )
        else:
            self.console.print_info(f"No intake files found in {self._watch_dir}")
            return

        # Process new files
        if new_count > 0:
            self.console.print_info(f"Processing {new_count} new file(s)...")
            for f in sorted(existing_files):
                file_hash = compute_file_hash(f)
                if file_hash and file_hash not in processed_hashes:
                    self._on_file_created(f)

    def _get_vlm(self):
        """Get or create VLM client (lazy initialization)."""
        if self._vlm is None:
            try:
                from gaia.llm import VLMClient

                self.console.print_model_loading(self._vlm_model)
                self._vlm = VLMClient(vlm_model=self._vlm_model)
                self.console.print_model_ready(self._vlm_model)
                logger.debug(f"VLM client initialized: {self._vlm_model}")
            except Exception as e:
                logger.error(f"Failed to initialize VLM: {e}")
                return None
        return self._vlm

    def _on_file_created(self, path: str) -> None:
        """Handle new file creation in watched directory."""
        file_path = Path(path)

        # Wait for file to be fully written (Windows file locking)
        time.sleep(0.5)

        try:
            size = file_path.stat().st_size
        except (FileNotFoundError, OSError):
            size = 0

        self.console.print_file_created(
            filename=file_path.name,
            size=size,
            extension=file_path.suffix,
        )

        # Process the file with retry for file locking issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._process_intake_form(path)
                break
            except PermissionError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"File locked, retrying in 2s ({attempt + 1}/{max_retries}): {e}"
                    )
                    time.sleep(2.0)
                else:
                    logger.error(
                        f"Failed to process file after {max_retries} attempts: {e}"
                    )
                    self.console.print_error(f"Could not access file: {file_path.name}")

    def _on_file_modified(self, path: str) -> None:
        """Handle file modification (re-process if needed)."""
        # Don't auto-reprocess modified files to avoid duplicates
        _ = path  # Intentionally unused - modifications don't trigger reprocessing

    def _emit_progress(
        self,
        filename: str,
        step_num: int,
        total_steps: int,
        step_name: str,
        status: str = "running",
    ) -> None:
        """
        Emit progress update to console and optional callback.

        Args:
            filename: Name of file being processed
            step_num: Current step number (1-based)
            total_steps: Total number of processing steps
            step_name: Human-readable step name
            status: 'running', 'complete', or 'error'
        """
        # Update console
        self.console.print_processing_step(step_num, total_steps, step_name, status)

        # Call external callback if registered (e.g., for SSE events)
        if self._progress_callback:
            try:
                self._progress_callback(
                    filename, step_num, total_steps, step_name, status
                )
            except Exception as e:
                logger.debug(f"Progress callback error: {e}")

    def _process_intake_form(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Process an intake form and extract patient data.

        Args:
            file_path: Path to the intake form (image or PDF)

        Returns:
            Extracted patient data dict, or None if extraction failed
        """
        path = Path(file_path)
        start_time = time.time()
        self._stats["files_processed"] += 1
        filename = path.name
        total_steps = 7  # Total processing steps

        logger.debug(f"Processing intake form: {filename}")

        # Start pipeline progress display
        self.console.print_processing_pipeline_start(filename, total_steps)

        try:
            # Step 1: Read file
            self._emit_progress(filename, 1, total_steps, "Reading file")
            try:
                with open(path, "rb") as f:
                    file_content = f.read()
            except (OSError, IOError) as e:
                logger.error(f"Could not read file: {e}")
                self._emit_progress(filename, 1, total_steps, "Reading file", "error")
                self._stats["extraction_failed"] += 1
                return None

            # Step 2: Check for duplicates
            self._emit_progress(filename, 2, total_steps, "Checking for duplicates")
            file_hash = compute_file_hash(path)
            if file_hash:
                existing = self.query(
                    "SELECT id, first_name, last_name FROM patients WHERE file_hash = ?",
                    (file_hash,),
                )
                if existing:
                    patient = existing[0]
                    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
                    self.console.print_info(
                        f"Skipping duplicate file (hash: {file_hash[:8]}...) - "
                        f"Already processed as patient: {name} (ID: {patient['id']})"
                    )
                    # Emit duplicate event for Live Feed
                    self._emit_progress(
                        filename,
                        2,
                        total_steps,
                        f"Duplicate - already processed as {name}",
                        "duplicate",
                    )
                    # Show completion in console
                    self.console.print_processing_pipeline_complete(
                        filename,
                        True,
                        time.time() - start_time,
                        name,
                        is_duplicate=True,
                    )
                    return None

            # Step 3: Prepare and optimize image
            self._emit_progress(filename, 3, total_steps, "Optimizing image")
            image_bytes = self._read_file_as_image(path)
            if image_bytes is None:
                self._emit_progress(
                    filename, 3, total_steps, "Optimizing image", "error"
                )
                self._stats["extraction_failed"] += 1
                return None

            # Step 4: Load VLM model
            self._emit_progress(filename, 4, total_steps, "Loading AI model")
            vlm = self._get_vlm()
            if vlm is None:
                logger.error("VLM not available")
                self._emit_progress(
                    filename, 4, total_steps, "Loading AI model", "error"
                )
                self._stats["extraction_failed"] += 1
                return None

            # Step 5: Extract data with VLM
            self._emit_progress(filename, 5, total_steps, "Extracting patient data")
            mime_type = detect_image_mime_type(image_bytes)
            size_kb = len(image_bytes) / 1024
            self.console.print_extraction_start(1, 1, mime_type)

            extraction_start = time.time()
            raw_text = vlm.extract_from_image(
                image_bytes=image_bytes,
                prompt=EXTRACTION_PROMPT,
            )
            extraction_time = time.time() - extraction_start

            # Check for VLM extraction errors (surfaced to user)
            if raw_text.startswith("[VLM extraction failed:"):
                # Extract the error message from the marker
                error_msg = raw_text[1:-1] if raw_text.endswith("]") else raw_text
                self.console.print_error(f"❌ {error_msg}")
                logger.error(f"VLM extraction failed for {path.name}: {error_msg}")
                self._emit_progress(
                    filename, 5, total_steps, "Extracting patient data", "error"
                )
                self._stats["extraction_failed"] += 1
                return None

            self.console.print_extraction_complete(
                len(raw_text), 1, extraction_time, size_kb
            )

            # Step 6: Parse extraction
            self._emit_progress(filename, 6, total_steps, "Parsing extracted data")
            patient_data = self._parse_extraction(raw_text)
            if patient_data is None:
                logger.warning(f"Failed to parse extraction for: {path.name}")
                self._emit_progress(
                    filename, 6, total_steps, "Parsing extracted data", "error"
                )
                self._stats["extraction_failed"] += 1
                return None

            # Add metadata including file content and hash
            patient_data["source_file"] = str(path.absolute())
            patient_data["raw_extraction"] = raw_text
            patient_data["file_hash"] = file_hash
            patient_data["file_content"] = file_content

            # Check for returning patient (by name/DOB, not file hash)
            existing_patient = self._find_existing_patient(patient_data)
            is_new_patient = existing_patient is None
            changes_detected = []

            if existing_patient:
                # Detect changes for returning patient
                changes_detected = self._detect_changes(existing_patient, patient_data)
                patient_data["is_new_patient"] = False
                self._stats["returning_patients"] += 1
            else:
                patient_data["is_new_patient"] = True
                self._stats["new_patients"] += 1

            # Calculate processing time
            processing_time = time.time() - start_time
            patient_data["processing_time_seconds"] = processing_time
            self._stats["total_processing_time_seconds"] += processing_time

            # Calculate estimated manual entry time based on extracted data
            estimated_manual = estimate_manual_entry_time(patient_data)
            patient_data["estimated_manual_seconds"] = estimated_manual
            self._stats["total_estimated_manual_seconds"] += estimated_manual

            # Step 7: Save to database
            self._emit_progress(filename, 7, total_steps, "Saving to database")
            if existing_patient:
                patient_id = self._update_patient(existing_patient["id"], patient_data)
            else:
                patient_id = self._store_patient(patient_data)

            if patient_id:
                self._stats["extraction_success"] += 1
                patient_data["id"] = patient_id
                patient_data["changes_detected"] = changes_detected

                # Record intake session for audit trail
                self._record_intake_session(
                    patient_id, path, processing_time, is_new_patient, changes_detected
                )

                # Create alerts for critical items
                self._create_alerts(patient_id, patient_data)

                self._processed_files.append(
                    {
                        "file": path.name,
                        "patient_id": patient_id,
                        "name": f"{patient_data.get('first_name', '')} {patient_data.get('last_name', '')}",
                        "is_new_patient": is_new_patient,
                        "changes_detected": changes_detected,
                        "processing_time_seconds": processing_time,
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

                # Limit memory usage - keep only last 1000 entries
                if len(self._processed_files) > 1000:
                    self._processed_files = self._processed_files[-1000:]

                # Show pipeline completion
                patient_name = f"{patient_data.get('first_name', '')} {patient_data.get('last_name', '')}".strip()
                self.console.print_processing_pipeline_complete(
                    filename, True, processing_time, patient_name
                )

                status = "NEW" if is_new_patient else "RETURNING"
                self.console.print_success(
                    f"[{status}] Patient record: {patient_data.get('first_name')} "
                    f"{patient_data.get('last_name')} (ID: {patient_id})"
                )

                # Display extracted patient details
                self._print_patient_details(
                    patient_data, changes_detected, is_new_patient
                )

                return patient_data

        except Exception as e:
            logger.error(f"Error processing {path.name}: {e}")
            self.console.print_processing_pipeline_complete(
                filename, False, time.time() - start_time
            )
            self._stats["extraction_failed"] += 1

        return None

    def _print_patient_details(
        self, data: Dict[str, Any], changes: List[Dict[str, Any]], is_new: bool = True
    ) -> None:
        """Print extracted patient details to console using Rich formatting."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        # Fields to skip in display (especially binary/large data)
        skip_fields = {
            "id",
            "source_file",
            "raw_extraction",
            "additional_fields",
            "is_new_patient",
            "processing_time_seconds",
            "changes_detected",
            "created_at",
            "updated_at",
            "file_content",  # Binary image data
            "file_hash",  # Hash string
        }

        # Group fields by category with icons
        categories = {
            "👤 Identity": [
                "first_name",
                "last_name",
                "date_of_birth",
                "gender",
                "ssn",
            ],
            "📞 Contact": [
                "phone",
                "mobile_phone",
                "email",
                "address",
                "city",
                "state",
                "zip_code",
            ],
            "🏥 Insurance": ["insurance_provider", "insurance_id", "insurance_group"],
            "💊 Medical": [
                "reason_for_visit",
                "allergies",
                "medications",
                "date_of_injury",
            ],
            "🆘 Emergency": ["emergency_contact_name", "emergency_contact_phone"],
            "💼 Employment": ["employer", "occupation", "work_related_injury"],
            "👨‍⚕️ Provider": ["referring_physician"],
        }

        # Track changed fields for highlighting
        changed_fields = {c["field"] for c in changes} if changes else set()

        # Create table for patient details
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Field", style="dim")
        table.add_column("Value")

        displayed_fields = set()
        field_count = 0

        for category, fields in categories.items():
            category_rows = []
            for field in fields:
                value = data.get(field)
                if value is not None and value != "" and value != "null":
                    displayed_fields.add(field)
                    field_count += 1
                    # Handle boolean values
                    if isinstance(value, bool):
                        value = "Yes" if value else "No"
                    # Style changed fields
                    if field in changed_fields:
                        category_rows.append(
                            (field, f"[bold yellow]{value}[/bold yellow] *")
                        )
                    else:
                        category_rows.append((field, str(value)))

            if category_rows:
                # Add category header
                table.add_row(f"[bold cyan]{category}[/bold cyan]", "")
                for field, value in category_rows:
                    table.add_row(f"  {field}", value)

        # Show additional fields not in categories
        all_category_fields = set()
        for fields in categories.values():
            all_category_fields.update(fields)

        extra_rows = []
        for key, value in data.items():
            if key not in all_category_fields and key not in skip_fields:
                if value is not None and value != "" and value != "null":
                    displayed_fields.add(key)
                    field_count += 1
                    if isinstance(value, bool):
                        value = "Yes" if value else "No"
                    if key in changed_fields:
                        extra_rows.append(
                            (key, f"[bold yellow]{value}[/bold yellow] *")
                        )
                    else:
                        extra_rows.append((key, str(value)))

        if extra_rows:
            table.add_row("[bold cyan]📋 Additional[/bold cyan]", "")
            for field, value in extra_rows:
                table.add_row(f"  {field}", value)

        # Print patient details in a panel
        console.print(Panel(table, title="Extracted Fields", border_style="blue"))

        # Summary for returning patients
        if not is_new:
            if changed_fields:
                console.print(
                    f"[yellow]⚠️  {len(changed_fields)} field(s) changed:[/yellow] "
                    f"{', '.join(changed_fields)}"
                )
            else:
                console.print("[green]✓ All fields identical to previous visit[/green]")
        else:
            console.print(f"[dim]{field_count} fields extracted[/dim]")

        # Show ready for input prompt
        self.console.print_ready_for_input()

    def _find_existing_patient(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if patient already exists in database."""
        if not data.get("first_name") or not data.get("last_name"):
            return None

        # Match on name + DOB (most reliable)
        if data.get("date_of_birth"):
            results = self.query(
                """SELECT * FROM patients
                   WHERE first_name = :fn AND last_name = :ln AND date_of_birth = :dob
                   ORDER BY created_at DESC LIMIT 1""",
                {
                    "fn": data["first_name"],
                    "ln": data["last_name"],
                    "dob": data["date_of_birth"],
                },
            )
            if results:
                return results[0]

        # Fallback: match on name only (less reliable)
        results = self.query(
            """SELECT * FROM patients
               WHERE first_name = :fn AND last_name = :ln
               ORDER BY created_at DESC LIMIT 1""",
            {"fn": data["first_name"], "ln": data["last_name"]},
        )
        return results[0] if results else None

    def _detect_changes(
        self, existing: Dict[str, Any], new_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Detect changes between existing patient and new data."""
        fields_to_compare = [
            "phone",
            "email",
            "address",
            "city",
            "state",
            "zip_code",
            "insurance_provider",
            "insurance_id",
            "medications",
            "allergies",
        ]
        return detect_field_changes(existing, new_data, fields_to_compare)

    def _update_patient(self, patient_id: int, data: Dict[str, Any]) -> Optional[int]:
        """Update existing patient record with flexible schema support."""
        try:
            # Separate standard fields from additional fields
            update_data = {}
            additional_fields = {}

            for key, value in data.items():
                if key in UPDATABLE_COLUMNS:
                    update_data[key] = value
                elif key not in ["first_name", "last_name", "date_of_birth", "gender"]:
                    # Don't override identity fields, but capture extras
                    if value is not None and value != "":
                        additional_fields[key] = value

            # Merge with existing additional_fields if any
            if additional_fields:
                # Get existing additional_fields
                existing = self.query(
                    "SELECT additional_fields FROM patients WHERE id = :id",
                    {"id": patient_id},
                )
                if existing and existing[0].get("additional_fields"):
                    try:
                        existing_extra = json.loads(existing[0]["additional_fields"])
                        existing_extra.update(additional_fields)
                        additional_fields = existing_extra
                    except json.JSONDecodeError:
                        pass

                update_data["additional_fields"] = json.dumps(additional_fields)
                logger.info(
                    f"Updating {len(additional_fields)} additional fields: "
                    f"{list(additional_fields.keys())}"
                )

            update_data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

            # Use mixin's update() method with proper signature
            self.update(
                "patients",
                update_data,
                "id = :id",
                {"id": patient_id},
            )
            logger.info(f"Updated patient record ID: {patient_id}")
            return patient_id

        except Exception as e:
            logger.error(f"Failed to update patient: {e}")
            return None

    def _record_intake_session(
        self,
        patient_id: int,
        path: Path,
        processing_time: float,
        is_new_patient: bool,
        changes_detected: List[Dict[str, Any]],
    ) -> None:
        """Record intake session for audit trail."""
        try:
            self.insert(
                "intake_sessions",
                {
                    "patient_id": patient_id,
                    "source_file": str(path.absolute()),
                    "processing_time_seconds": processing_time,
                    "is_new_patient": is_new_patient,
                    "changes_detected": (
                        json.dumps(changes_detected) if changes_detected else None
                    ),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to record intake session: {e}")

    def _create_alerts(self, patient_id: int, data: Dict[str, Any]) -> None:
        """Create alerts for critical items (allergies, missing fields)."""
        try:
            # Critical allergy alert (avoid duplicates for returning patients)
            if data.get("allergies"):
                # Check for existing unacknowledged allergy alert
                existing = self.query(
                    """SELECT id FROM alerts
                       WHERE patient_id = :pid AND alert_type = 'allergy'
                       AND acknowledged = FALSE""",
                    {"pid": patient_id},
                )
                if not existing:
                    self.insert(
                        "alerts",
                        {
                            "patient_id": patient_id,
                            "alert_type": "allergy",
                            "priority": "critical",
                            "message": f"Patient has allergies: {data['allergies']}",
                            "data": json.dumps({"allergies": data["allergies"]}),
                        },
                    )

            # Check for missing critical fields
            critical_fields = ["phone", "date_of_birth"]
            missing = [f for f in critical_fields if not data.get(f)]
            if missing:
                # Check for existing unacknowledged missing_field alert
                existing = self.query(
                    """SELECT id FROM alerts
                       WHERE patient_id = :pid AND alert_type = 'missing_field'
                       AND acknowledged = FALSE""",
                    {"pid": patient_id},
                )
                if not existing:
                    self.insert(
                        "alerts",
                        {
                            "patient_id": patient_id,
                            "alert_type": "missing_field",
                            "priority": "medium",
                            "message": f"Missing critical fields: {', '.join(missing)}",
                            "data": json.dumps({"missing_fields": missing}),
                        },
                    )

        except Exception as e:
            logger.warning(f"Failed to create alerts: {e}")

    def _read_file_as_image(self, path: Path) -> Optional[bytes]:
        """Read file and convert to optimized image bytes for VLM processing.

        Images are automatically resized if they exceed MAX_DIMENSION to improve
        processing speed while maintaining sufficient quality for OCR/extraction.
        """
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            # Convert PDF first page to image (already optimized in pdf_page_to_image)
            return self._pdf_to_image(path)
        elif suffix in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
            raw_bytes = path.read_bytes()
            return self._optimize_image(raw_bytes)
        else:
            logger.warning(f"Unsupported file type: {suffix}")
            return None

    def _optimize_image(
        self,
        image_bytes: bytes,
        max_dimension: int = 1024,
        jpeg_quality: int = 85,
    ) -> bytes:
        """
        Optimize image for VLM processing by resizing large images.

        Reduces image dimensions while maintaining quality sufficient for OCR
        and text extraction. This dramatically improves processing speed for
        high-resolution scans and photos.

        Images are padded to square dimensions to avoid a Vulkan backend bug
        in llama.cpp where the UPSCALE operator is unsupported for certain
        non-square aspect ratios (particularly landscape orientations).

        Args:
            image_bytes: Raw image bytes (PNG, JPEG, etc.)
            max_dimension: Maximum width or height (default: 1024px)
            jpeg_quality: JPEG compression quality 1-100 (default: 85)

        Returns:
            Optimized image bytes (JPEG format, square dimensions)
        """
        import io

        try:
            from PIL import Image, ImageOps

            # Load image from bytes
            img = Image.open(io.BytesIO(image_bytes))

            # Apply EXIF orientation - phone photos are often stored landscape
            # but have EXIF metadata indicating they should be displayed as portrait
            img = ImageOps.exif_transpose(img)

            original_width, original_height = img.size
            original_size_kb = len(image_bytes) / 1024

            # Convert to RGB early if needed (for JPEG output)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Check if resizing is needed
            if original_width <= max_dimension and original_height <= max_dimension:
                new_width, new_height = original_width, original_height
            else:
                # Calculate new dimensions maintaining aspect ratio
                scale = min(
                    max_dimension / original_width, max_dimension / original_height
                )
                new_width = int(original_width * scale)
                new_height = int(original_height * scale)

                # Resize with high-quality LANCZOS filter
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Pad to square to avoid Vulkan UPSCALE bug with non-square images
            # The bug causes timeouts with landscape orientations (e.g., 1024x768)
            if new_width != new_height:
                square_size = max(new_width, new_height)
                # Create white square canvas
                square_img = Image.new(
                    "RGB", (square_size, square_size), (255, 255, 255)
                )
                # Center the image on the canvas
                x_offset = (square_size - new_width) // 2
                y_offset = (square_size - new_height) // 2
                square_img.paste(img, (x_offset, y_offset))
                img = square_img
                final_size = square_size
                was_padded = True
            else:
                final_size = new_width
                was_padded = False

            # Save as optimized JPEG
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
            optimized_bytes = output.getvalue()

            optimized_size_kb = len(optimized_bytes) / 1024
            reduction_pct = (1 - optimized_size_kb / original_size_kb) * 100

            # Show optimization results to user
            if was_padded:
                self.console.print_info(
                    f"Image resized: {original_width}x{original_height} → "
                    f"{final_size}x{final_size} (padded to square, "
                    f"{original_size_kb:.0f}KB → {optimized_size_kb:.0f}KB, "
                    f"{reduction_pct:.0f}% smaller)"
                )
            else:
                self.console.print_info(
                    f"Image resized: {original_width}x{original_height} → "
                    f"{new_width}x{new_height} ({original_size_kb:.0f}KB → "
                    f"{optimized_size_kb:.0f}KB, {reduction_pct:.0f}% smaller)"
                )

            logger.info(
                f"Image optimized: {original_width}x{original_height} → "
                f"{final_size}x{final_size}, {original_size_kb:.0f}KB → "
                f"{optimized_size_kb:.0f}KB ({reduction_pct:.0f}% reduction)"
                f"{' (padded to square)' if was_padded else ''}"
            )

            return optimized_bytes

        except ImportError:
            logger.warning("PIL not available, returning original image")
            return image_bytes
        except Exception as e:
            logger.warning(f"Image optimization failed: {e}, using original")
            return image_bytes

    def _pdf_to_image(self, pdf_path: Path) -> Optional[bytes]:
        """Convert first page of PDF to image bytes."""
        return pdf_page_to_image(pdf_path, page=0, scale=2.0)

    def _parse_extraction(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """Parse extracted text into structured patient data."""
        result = extract_json_from_text(raw_text)
        if result is None:
            logger.warning("No valid JSON found in extraction")
            return None

        # Normalize phone fields: prefer mobile_phone if phone is not set
        # This handles forms where VLM extracts to mobile_phone instead of phone
        if not result.get("phone"):
            for phone_field in [
                "mobile_phone",
                "home_phone",
                "work_phone",
                "cell_phone",
            ]:
                if result.get(phone_field):
                    result["phone"] = result[phone_field]
                    logger.debug(
                        f"Normalized {phone_field} to phone: {result['phone']}"
                    )
                    break

        # Also check emergency_contact_phone normalization
        if not result.get("emergency_contact_phone"):
            for ec_phone in ["emergency_phone", "emergency_contact"]:
                if result.get(ec_phone) and isinstance(result[ec_phone], str):
                    # Check if it looks like a phone number
                    if any(c.isdigit() for c in result[ec_phone]):
                        result["emergency_contact_phone"] = result[ec_phone]
                        break

        return result

    def _store_patient(self, data: Dict[str, Any]) -> Optional[int]:
        """Store patient data in database with flexible schema support."""
        try:
            # Validate required fields
            if not data.get("first_name") or not data.get("last_name"):
                logger.error("Missing required fields: first_name and/or last_name")
                self.console.print_error("Cannot store patient: missing name fields")
                return None

            # Separate standard fields from additional fields
            insert_data = {}
            additional_fields = {}

            for key, value in data.items():
                if key in STANDARD_COLUMNS:
                    insert_data[key] = value
                elif value is not None and value != "":
                    # Store non-empty extra fields in additional_fields
                    additional_fields[key] = value

            # Store additional fields as JSON if any exist
            if additional_fields:
                insert_data["additional_fields"] = json.dumps(additional_fields)
                logger.info(
                    f"Storing {len(additional_fields)} additional fields: "
                    f"{list(additional_fields.keys())}"
                )

            patient_id = self.insert("patients", insert_data)
            logger.info(f"Stored patient record ID: {patient_id}")
            return patient_id

        except Exception as e:
            logger.error(f"Failed to store patient: {e}")
            self.console.print_error(f"Database error: {str(e)}")
            return None

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the intake agent."""
        return f"""You are a Medical Intake Assistant managing patient records.

You have access to a database of patient intake forms that were automatically processed.

**Your Capabilities:**
- Search for patients by name, DOB, or other criteria
- View patient details and intake information
- Report on processing statistics
- Answer questions about patient data

**Current Status:**
- Watching directory: {self._watch_dir}
- Database: {self._db_path}
- Files processed: {self._stats.get('files_processed', 0)}
- Successful extractions: {self._stats.get('extraction_success', 0)}

**Important:**
- Always protect patient privacy
- Only report data that was extracted from forms
- If asked about a patient not in the database, say so clearly

Use the available tools to search and retrieve patient information."""

    def _register_tools(self):
        """Register patient management tools."""
        agent = self

        @tool
        def search_patients(
            name: Optional[str] = None,
            date_of_birth: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Search for patients by name or date of birth.

            Args:
                name: Patient name (first, last, or full name)
                date_of_birth: Date of birth (YYYY-MM-DD format)

            Returns:
                Dict with matching patients
            """
            conditions = []
            params = {}

            if name:
                conditions.append(
                    "(first_name LIKE :name OR last_name LIKE :name "
                    "OR (first_name || ' ' || last_name) LIKE :name)"
                )
                params["name"] = f"%{name}%"

            if date_of_birth:
                conditions.append("date_of_birth = :dob")
                params["dob"] = date_of_birth

            if not conditions:
                # Return recent patients if no criteria
                query = """
                    SELECT id, first_name, last_name, date_of_birth,
                           phone, reason_for_visit, created_at
                    FROM patients
                    ORDER BY created_at DESC
                    LIMIT 10
                """
                params = {}
            else:
                query = f"""
                    SELECT id, first_name, last_name, date_of_birth,
                           phone, reason_for_visit, created_at
                    FROM patients
                    WHERE {' AND '.join(conditions)}
                    ORDER BY created_at DESC
                """

            results = agent.query(query, params)
            return {
                "patients": results,
                "count": len(results),
                "query": {"name": name, "date_of_birth": date_of_birth},
            }

        @tool
        def get_patient(patient_id: int) -> Dict[str, Any]:
            """
            Get full details for a specific patient.

            Args:
                patient_id: The patient's database ID

            Returns:
                Dict with patient details
            """
            results = agent.query(
                "SELECT * FROM patients WHERE id = :id",
                {"id": patient_id},
            )

            if results:
                patient = results[0]
                # Remove large/binary fields - don't send to LLM
                patient.pop("raw_extraction", None)
                patient.pop("file_content", None)  # Image bytes
                patient.pop("embedding", None)  # Vector embedding
                # Truncate file_hash for display
                if patient.get("file_hash"):
                    patient["file_hash"] = patient["file_hash"][:12] + "..."
                return {"found": True, "patient": patient}
            return {"found": False, "message": f"Patient ID {patient_id} not found"}

        @tool
        def list_recent_patients(limit: int = 10) -> Dict[str, Any]:
            """
            List recently processed patients.

            Args:
                limit: Maximum number of patients to return (default: 10)

            Returns:
                Dict with recent patients
            """
            results = agent.query(
                """
                SELECT id, first_name, last_name, date_of_birth,
                       reason_for_visit, created_at
                FROM patients
                ORDER BY created_at DESC
                LIMIT :limit
                """,
                {"limit": limit},
            )
            return {"patients": results, "count": len(results)}

        @tool
        def get_intake_stats() -> Dict[str, Any]:
            """
            Get statistics about intake form processing.

            Returns:
                Dict with processing statistics
            """
            return agent.get_stats()

        @tool
        def process_file(file_path: str) -> Dict[str, Any]:
            """
            Manually process an intake form file.

            Args:
                file_path: Path to the intake form file

            Returns:
                Dict with processing result
            """
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"File not found: {file_path}"}

            # pylint: disable=protected-access
            result = agent._process_intake_form(str(path))
            if result:
                return {
                    "success": True,
                    "patient_id": result.get("id"),
                    "name": f"{result.get('first_name', '')} {result.get('last_name', '')}",
                }
            return {"success": False, "error": "Failed to extract patient data"}

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about intake form processing.

        Returns:
            Dict with processing statistics including:
            - total_patients: Total patient count
            - processed_today: Patients processed today
            - new_patients: New patient count
            - returning_patients: Returning patient count
            - files_processed: Total files processed
            - extraction_success/failed: Success/failure counts
            - success_rate: Success percentage
            - time_saved_minutes/percent: Time savings metrics
            - avg_processing_seconds: Average processing time
            - unacknowledged_alerts: Alert count
            - watching_directory: Watched directory path
            - uptime_seconds: Agent uptime
        """
        # Get total patient count
        count_result = self.query("SELECT COUNT(*) as count FROM patients")
        total_patients = count_result[0]["count"] if count_result else 0

        # Get today's count
        today_result = self.query(
            "SELECT COUNT(*) as count FROM patients WHERE date(created_at) = date('now')"
        )
        today_count = today_result[0]["count"] if today_result else 0

        # Get unacknowledged alerts count
        alerts_result = self.query(
            "SELECT COUNT(*) as count FROM alerts WHERE acknowledged = FALSE"
        )
        unacknowledged_alerts = alerts_result[0]["count"] if alerts_result else 0

        # Calculate time savings based on actual extracted data
        # Uses estimated manual entry time calculated per-form based on fields/characters
        estimated_manual_seconds = self._stats["total_estimated_manual_seconds"]
        actual_processing_seconds = self._stats["total_processing_time_seconds"]
        time_saved_seconds = max(
            0, estimated_manual_seconds - actual_processing_seconds
        )

        # Calculate percentage improvement
        if estimated_manual_seconds > 0:
            time_saved_percent = (time_saved_seconds / estimated_manual_seconds) * 100
        else:
            time_saved_percent = 0

        # Average estimated manual time per form
        successful = self._stats["extraction_success"]
        avg_manual_seconds = (
            estimated_manual_seconds / successful if successful > 0 else 0
        )

        return {
            "total_patients": total_patients,
            "processed_today": today_count,
            "new_patients": self._stats["new_patients"],
            "returning_patients": self._stats["returning_patients"],
            "files_processed": self._stats["files_processed"],
            "extraction_success": self._stats["extraction_success"],
            "extraction_failed": self._stats["extraction_failed"],
            "success_rate": (
                f"{(self._stats['extraction_success'] / self._stats['files_processed'] * 100):.1f}%"
                if self._stats["files_processed"] > 0
                else "N/A"
            ),
            # Total cumulative metrics
            "time_saved_seconds": round(time_saved_seconds, 1),
            "time_saved_minutes": round(time_saved_seconds / 60, 1),
            "time_saved_percent": f"{time_saved_percent:.0f}%",
            "total_estimated_manual_seconds": round(estimated_manual_seconds, 1),
            "total_ai_processing_seconds": round(actual_processing_seconds, 1),
            # Per-form averages
            "avg_manual_seconds": round(avg_manual_seconds, 1),
            "avg_processing_seconds": (
                round(actual_processing_seconds / successful, 1)
                if successful > 0
                else 0
            ),
            # Legacy field name for backwards compatibility
            "estimated_manual_seconds": round(estimated_manual_seconds, 1),
            "unacknowledged_alerts": unacknowledged_alerts,
            "watching_directory": str(self._watch_dir),
            "uptime_seconds": int(time.time() - self._stats["start_time"]),
        }

    def clear_database(self) -> Dict[str, Any]:
        """
        Clear all data from the database and reset statistics.

        This removes all patients, alerts, and intake sessions, providing
        a clean slate for fresh processing.

        Returns:
            Dict with counts of deleted records
        """
        logger.info("Clearing database...")
        counts = {}

        try:
            # Get counts before deletion
            for table in ["patients", "alerts", "intake_sessions"]:
                result = self.query(f"SELECT COUNT(*) as count FROM {table}")
                counts[table] = result[0]["count"] if result else 0

            # Delete all records from each table
            self.execute("DELETE FROM intake_sessions")
            self.execute("DELETE FROM alerts")
            self.execute("DELETE FROM patients")

            # Reset in-memory statistics
            self._stats = {
                "files_processed": 0,
                "extraction_success": 0,
                "extraction_failed": 0,
                "new_patients": 0,
                "returning_patients": 0,
                "total_processing_time_seconds": 0.0,
                "total_estimated_manual_seconds": 0.0,
                "start_time": time.time(),
            }

            # Clear processed files list
            self._processed_files = []

            logger.info(
                f"Database cleared: {counts.get('patients', 0)} patients, "
                f"{counts.get('alerts', 0)} alerts, "
                f"{counts.get('intake_sessions', 0)} sessions"
            )

            return {
                "success": True,
                "deleted": counts,
                "message": "Database cleared successfully",
            }

        except Exception as e:
            logger.error(f"Failed to clear database: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Failed to clear database",
            }

    def stop(self) -> None:
        """Stop the agent and clean up resources."""
        logger.info("Stopping Medical Intake Agent...")
        errors = []

        # Stop file watchers
        try:
            self.stop_all_watchers()
        except Exception as e:
            errors.append(f"Failed to stop watchers: {e}")
            logger.error(errors[-1])

        # Close database
        try:
            self.close_db()
        except Exception as e:
            errors.append(f"Failed to close database: {e}")
            logger.error(errors[-1])

        # Cleanup VLM
        try:
            if self._vlm:
                self._vlm.cleanup()
                self._vlm = None
        except Exception as e:
            errors.append(f"Failed to cleanup VLM: {e}")
            logger.error(errors[-1])

        if errors:
            logger.warning(f"Cleanup completed with {len(errors)} error(s)")
        else:
            logger.info("Medical Intake Agent stopped")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.stop()
        return False
