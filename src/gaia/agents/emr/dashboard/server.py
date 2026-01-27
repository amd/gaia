# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastAPI server for EMR Dashboard with SSE support."""

# pylint: disable=protected-access
# Dashboard server intentionally accesses agent internals to hook into
# processing events, patch methods for SSE notifications, and read config.

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

try:
    import uvicorn
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, Response, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    # Placeholders for when FastAPI is not installed
    uvicorn = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment,misc]
    File = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment,misc]
    UploadFile = None  # type: ignore[assignment]
    CORSMiddleware = None  # type: ignore[assignment]
    FileResponse = None  # type: ignore[assignment]
    Response = None  # type: ignore[assignment]
    StreamingResponse = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]

from gaia.agents.emr.agent import MedicalIntakeAgent

logger = logging.getLogger(__name__)


def _safe_json_default(obj: Any) -> Any:
    """Fallback serializer for non-standard JSON types."""
    if isinstance(obj, bytes):
        return f"<binary: {len(obj)} bytes>"
    elif hasattr(obj, "isoformat"):
        return obj.isoformat()
    elif hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _safe_json_dumps(obj: Any) -> str:
    """JSON dumps with fallback for non-serializable types like bytes."""
    return json.dumps(obj, default=_safe_json_default)


# Pydantic models for request validation
class WatchDirConfig(BaseModel):
    """Request model for watch directory configuration."""

    watch_dir: str


class ChatRequest(BaseModel):
    """Request model for chat messages."""

    message: str


class PatientUpdateRequest(BaseModel):
    """Request model for updating patient data."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_id: Optional[str] = None
    reason_for_visit: Optional[str] = None
    allergies: Optional[str] = None
    medications: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


# Global state
_agent_instance: Optional[MedicalIntakeAgent] = None
_agent_lock = threading.Lock()

# Per-client SSE queues for multi-client broadcast
_sse_clients: List[asyncio.Queue] = []
_sse_clients_lock = asyncio.Lock()

# Store the main event loop reference for thread-safe broadcasting
_main_event_loop: Optional[asyncio.AbstractEventLoop] = None

# Track currently processing file for status display
_current_processing_file: Optional[str] = None
_processing_lock = threading.Lock()

# Track files being processed via upload API (to prevent file watcher double-processing)
_api_processing_files: Set[str] = set()
_api_processing_lock = threading.Lock()

# Thread-local storage to mark API-initiated processing (skip the duplicate check)
_thread_local = threading.local()

# Track failed file hashes to show "failed" status in watch folder
_failed_file_hashes: Set[str] = set()
_failed_lock = threading.Lock()

# Ring buffer of recent events to replay to new SSE clients
_recent_events: List[Dict[str, Any]] = []
_recent_events_lock = threading.Lock()  # Use threading.Lock for cross-thread access
_MAX_RECENT_EVENTS = 20  # Keep last 20 events for replay


async def broadcast_event(event: Dict[str, Any]) -> None:
    """Broadcast event to all SSE clients and store for replay."""
    # Store in recent events buffer (only patient_created events for replay)
    if event.get("type") == "patient_created":
        with _recent_events_lock:
            _recent_events.append(event)
            # Keep only the most recent events
            if len(_recent_events) > _MAX_RECENT_EVENTS:
                _recent_events.pop(0)

    async with _sse_clients_lock:
        for client_queue in _sse_clients:
            try:
                client_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Client queue full, dropping event")


def _broadcast_sync(event: Dict[str, Any]) -> None:
    """Thread-safe helper to broadcast event from any context (including file watcher thread)."""
    if _main_event_loop is None:
        logger.warning("Main event loop not set, cannot broadcast event")
        return

    try:
        # Use the stored main event loop reference for thread-safe broadcasting
        asyncio.run_coroutine_threadsafe(broadcast_event(event), _main_event_loop)
    except Exception as e:
        logger.error(f"Error broadcasting event: {e}")


class DashboardEventHandler:
    """Handler that publishes agent events to SSE clients."""

    @staticmethod
    def on_patient_created(patient_data: Dict[str, Any]):
        """Publish patient created event."""
        event = {
            "type": "patient_created",
            "data": patient_data,
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)

    @staticmethod
    def on_processing_started(filename: str):
        """Publish processing started event."""
        event = {
            "type": "processing_started",
            "data": {"filename": filename},
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)

    @staticmethod
    def on_processing_completed(
        filename: str,
        success: bool,
        patient_id: Optional[int] = None,
        is_duplicate: bool = False,
        patient_name: str = None,
    ):
        """Publish processing completed event."""
        event = {
            "type": "processing_completed",
            "data": {
                "filename": filename,
                "success": success,
                "patient_id": patient_id,
                "is_duplicate": is_duplicate,
                "patient_name": patient_name,
            },
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)

    @staticmethod
    def on_status_update(filename: str, status: str, detail: str = ""):
        """Publish processing status update."""
        event = {
            "type": "status_update",
            "data": {
                "filename": filename,
                "status": status,
                "detail": detail,
            },
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)

    @staticmethod
    def on_processing_error(filename: str, error: str, error_type: str = "error"):
        """Publish processing error event."""
        event = {
            "type": "processing_error",
            "data": {
                "filename": filename,
                "error": error,
                "error_type": error_type,
            },
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)

    @staticmethod
    def on_processing_step(
        filename: str,
        step_num: int,
        total_steps: int,
        step_name: str,
        status: str = "running",
    ):
        """Publish processing step event for progress tracking."""
        event = {
            "type": "processing_step",
            "data": {
                "filename": filename,
                "step_num": step_num,
                "total_steps": total_steps,
                "step_name": step_name,
                "status": status,
            },
            "timestamp": datetime.now().isoformat(),
        }
        _broadcast_sync(event)


def create_app(
    watch_dir: str = "./intake_forms",
    db_path: str = "./data/patients.db",
) -> FastAPI:
    """
    Create FastAPI app for EMR dashboard.

    Args:
        watch_dir: Directory to watch for intake forms
        db_path: Path to patient database

    Returns:
        FastAPI application instance
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Install with: pip install 'amd-gaia[api]'"
        )

    app = FastAPI(
        title="GAIA Medical Intake Dashboard",
        description="Real-time patient intake monitoring with AMD Ryzen AI",
        version="0.1.0",
    )

    # Enable CORS for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def start_agent():
        """Start agent in background thread."""
        global _agent_instance

        # Wait for dashboard/Electron to connect before processing files
        # This gives the UI time to establish SSE connection
        logger.info("Waiting 2s for dashboard to connect...")
        time.sleep(2.0)

        with _agent_lock:
            if _agent_instance is not None:
                logger.warning("Agent already initialized")
                return

            # Initialize with auto_start_watching=False, then start manually
            # This ensures SSE clients are connected before processing begins
            _agent_instance = MedicalIntakeAgent(
                watch_dir=watch_dir,
                db_path=db_path,
                auto_start_watching=False,
            )

            # Patch agent to publish events
            original_process = _agent_instance._process_intake_form
            original_get_vlm = _agent_instance._get_vlm
            original_store_patient = _agent_instance._store_patient

            # Track current file being processed for context
            _current_file = {"name": None}

            def patched_get_vlm():
                filename = _current_file.get("name", "file")
                if _agent_instance._vlm is None:
                    DashboardEventHandler.on_processing_step(
                        filename, 4, 7, "Loading AI model", "running"
                    )
                result = original_get_vlm()
                if result and _agent_instance._vlm is not None:
                    DashboardEventHandler.on_processing_step(
                        filename, 5, 7, "Extracting data", "running"
                    )
                return result

            def patched_store_patient(data):
                filename = _current_file.get("name", "file")
                # Emit storing step
                DashboardEventHandler.on_processing_step(
                    filename, 7, 7, "Saving to database", "running"
                )
                # Check for missing required fields before calling original
                if not data.get("first_name") or not data.get("last_name"):
                    DashboardEventHandler.on_processing_error(
                        filename,
                        "Missing required fields: first_name and/or last_name",
                        "validation_error",
                    )
                return original_store_patient(data)

            # Track duplicate detection per file
            _duplicate_info = {"is_duplicate": False, "patient_name": None}

            def patched_process(file_path: str):
                global _current_processing_file
                nonlocal _duplicate_info
                filename = Path(file_path).name
                _current_file["name"] = filename
                _duplicate_info = {"is_duplicate": False, "patient_name": None}

                # Skip if file is being processed via upload API (prevents double-processing)
                # But don't skip if this IS the API call (marked via thread-local flag)
                is_api_call = getattr(_thread_local, "is_api_call", False)
                if not is_api_call:
                    with _api_processing_lock:
                        if filename in _api_processing_files:
                            logger.info(
                                f"Skipping {filename} - already being processed via API"
                            )
                            return None

                # Compute file hash early for failed file tracking
                current_file_hash = None
                try:
                    from gaia.utils import compute_file_hash

                    current_file_hash = compute_file_hash(Path(file_path))
                except Exception:
                    pass

                # Track current processing file globally
                with _processing_lock:
                    _current_processing_file = filename

                DashboardEventHandler.on_processing_started(filename)

                try:
                    # Step 1: Reading file
                    DashboardEventHandler.on_processing_step(
                        filename, 1, 7, "Reading file", "running"
                    )

                    result = original_process(file_path)

                    if result:
                        patient_id = result.get("id")

                        # Fetch actual saved patient record from database
                        # This ensures SSE event matches database (handles additional_fields, etc.)
                        if patient_id and _agent_instance:
                            try:
                                db_record = _agent_instance.query(
                                    "SELECT * FROM patients WHERE id = :id",
                                    {"id": patient_id},
                                )
                                if db_record:
                                    # Use database record as base, merge extraction metadata
                                    event_data = dict(db_record[0])
                                    # Add extraction-only fields not in DB
                                    for key in [
                                        "is_new_patient",
                                        "changes_detected",
                                        "processing_time_seconds",
                                        "estimated_manual_seconds",
                                    ]:
                                        if key in result:
                                            event_data[key] = result[key]
                                else:
                                    event_data = result.copy()
                            except Exception as e:
                                logger.warning(f"Failed to fetch saved patient: {e}")
                                event_data = result.copy()
                        else:
                            event_data = result.copy()

                        # Unpack additional_fields JSON if present
                        if event_data.get("additional_fields"):
                            try:
                                additional = json.loads(event_data["additional_fields"])
                                for key, value in additional.items():
                                    if key not in event_data:
                                        event_data[key] = value
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Remove large fields from event
                        excluded_fields = {
                            "raw_extraction",
                            "file_content",
                            "additional_fields",
                        }
                        event_data = {
                            k: v
                            for k, v in event_data.items()
                            if k not in excluded_fields
                        }
                        # Truncate file_hash for display
                        if event_data.get("file_hash"):
                            event_data["file_hash"] = (
                                event_data["file_hash"][:12] + "..."
                            )
                        DashboardEventHandler.on_patient_created(event_data)
                        DashboardEventHandler.on_processing_completed(
                            filename, True, patient_id
                        )
                    elif _duplicate_info["is_duplicate"]:
                        # Result is None but duplicate was detected - this is success, not failure
                        DashboardEventHandler.on_processing_completed(
                            filename,
                            True,
                            None,
                            is_duplicate=True,
                            patient_name=_duplicate_info.get("patient_name"),
                        )
                    else:
                        # Result is None - actual extraction failure
                        # Track failed file hash for watch folder status
                        if current_file_hash:
                            with _failed_lock:
                                _failed_file_hashes.add(current_file_hash)
                        DashboardEventHandler.on_processing_completed(
                            filename, False, None
                        )
                    return result

                except Exception as e:
                    # Track failed file hash for watch folder status
                    if current_file_hash:
                        with _failed_lock:
                            _failed_file_hashes.add(current_file_hash)
                    DashboardEventHandler.on_processing_error(
                        filename, str(e), "exception"
                    )
                    DashboardEventHandler.on_processing_completed(filename, False)
                    raise
                finally:
                    _current_file["name"] = None
                    # Clear current processing file
                    with _processing_lock:
                        _current_processing_file = None

            _agent_instance._process_intake_form = patched_process
            _agent_instance._get_vlm = patched_get_vlm
            _agent_instance._store_patient = patched_store_patient

            # Register progress callback for SSE events
            def progress_callback(filename, step_num, total_steps, step_name, status):
                # Track duplicate status for completion event
                if status == "duplicate":
                    _duplicate_info["is_duplicate"] = True
                    # Extract patient name from step_name (format: "Duplicate - already processed as Name")
                    if "already processed as" in step_name:
                        _duplicate_info["patient_name"] = step_name.split(
                            "already processed as"
                        )[-1].strip()
                DashboardEventHandler.on_processing_step(
                    filename, step_num, total_steps, step_name, status
                )

            _agent_instance._progress_callback = progress_callback

            # Now start file watching (will process existing files)
            logger.info("Starting file watching...")
            # Initialize recent events buffer from existing patients
            # This ensures the live feed is populated even on server restart
            try:
                results = _agent_instance.query(
                    """SELECT id, first_name, last_name, is_new_patient,
                              processing_time_seconds, source_file, created_at
                       FROM patients ORDER BY created_at DESC LIMIT 10"""
                )
                if results:
                    with _recent_events_lock:
                        for row in reversed(
                            results
                        ):  # Add oldest first so newest is last
                            event = {
                                "type": "patient_created",
                                "timestamp": row.get("created_at", ""),
                                "data": {
                                    "id": row.get("id"),
                                    "first_name": row.get("first_name"),
                                    "last_name": row.get("last_name"),
                                    "is_new_patient": row.get("is_new_patient"),
                                    "processing_time_seconds": row.get(
                                        "processing_time_seconds"
                                    ),
                                    "source_file": row.get("source_file"),
                                    "filename": row.get("source_file"),
                                },
                            }
                            _recent_events.append(event)
                    logger.info(
                        f"Initialized live feed with {len(results)} recent patients"
                    )
            except Exception as e:
                logger.warning(f"Could not initialize recent events: {e}")

            _agent_instance._start_file_watching()

            logger.info("Agent started in background")

    # Start agent on startup
    @app.on_event("startup")
    async def startup_event():
        global _main_event_loop
        # Store reference to main event loop for thread-safe SSE broadcasting
        _main_event_loop = asyncio.get_running_loop()
        logger.info("Main event loop captured for SSE broadcasting")

        thread = threading.Thread(target=start_agent, daemon=True)
        thread.start()

    @app.on_event("shutdown")
    async def shutdown_event():
        global _agent_instance
        with _agent_lock:
            if _agent_instance:
                _agent_instance.stop()
                _agent_instance = None

    # API Routes

    @app.get("/api/patients")
    async def list_patients(
        limit: int = 100,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List patients with pagination and search."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Use basic columns that are guaranteed to exist, handle optional columns gracefully
            # Include changes_detected from most recent intake_session via subquery
            base_query = """
                SELECT p.id, p.first_name, p.last_name, p.date_of_birth, p.phone,
                       p.reason_for_visit, p.is_new_patient, p.created_at, p.file_hash,
                       p.processing_time_seconds, p.source_file, p.allergies,
                       p.insurance_provider, p.gender,
                       (SELECT s.changes_detected FROM intake_sessions s
                        WHERE s.patient_id = p.id
                        ORDER BY s.created_at DESC LIMIT 1) as changes_detected
                FROM patients p
            """
            if search:
                results = _agent_instance.query(
                    base_query + """
                    WHERE p.first_name LIKE :search OR p.last_name LIKE :search
                    ORDER BY p.created_at DESC
                    LIMIT :limit OFFSET :offset
                    """,
                    {"search": f"%{search}%", "limit": limit, "offset": offset},
                )
            else:
                results = _agent_instance.query(
                    base_query + """
                    ORDER BY p.created_at DESC
                    LIMIT :limit OFFSET :offset
                    """,
                    {"limit": limit, "offset": offset},
                )

            # Try to get estimated_manual_seconds if the column exists
            try:
                extended_results = _agent_instance.query(
                    "SELECT id, estimated_manual_seconds FROM patients WHERE id IN ("
                    + ",".join(str(r["id"]) for r in results)
                    + ")"
                    if results
                    else "SELECT 1 WHERE 0"
                )
                manual_times = {
                    r["id"]: r.get("estimated_manual_seconds") for r in extended_results
                }
                for r in results:
                    r["estimated_manual_seconds"] = manual_times.get(r["id"])
            except Exception:
                # Column doesn't exist, set to None
                for r in results:
                    r["estimated_manual_seconds"] = None

            # Process results: truncate file_hash and parse changes_detected JSON
            for patient in results:
                if patient.get("file_hash"):
                    patient["file_hash"] = patient["file_hash"][:12] + "..."
                # Parse changes_detected from JSON string if present
                if patient.get("changes_detected"):
                    try:
                        patient["changes_detected"] = json.loads(
                            patient["changes_detected"]
                        )
                    except (json.JSONDecodeError, TypeError):
                        patient["changes_detected"] = None

            count_result = _agent_instance.query(
                "SELECT COUNT(*) as count FROM patients"
            )
            total = count_result[0]["count"] if count_result else 0

            return {
                "patients": results,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        except Exception as e:
            logger.error(f"Error listing patients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/patients/{patient_id}")
    async def get_patient(patient_id: int) -> Dict[str, Any]:
        """Get patient details by ID."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            results = _agent_instance.query(
                "SELECT * FROM patients WHERE id = :id",
                {"id": patient_id},
            )

            if not results:
                raise HTTPException(status_code=404, detail="Patient not found")

            patient = results[0]
            # Remove large fields from API response
            patient.pop("raw_extraction", None)
            patient.pop("file_content", None)
            # Truncate file_hash for display
            if patient.get("file_hash"):
                patient["file_hash"] = patient["file_hash"][:12] + "..."
            return patient
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting patient: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/patients/{patient_id}")
    async def update_patient(
        patient_id: int, request: PatientUpdateRequest
    ) -> Dict[str, Any]:
        """Update patient details. Only provided fields will be updated."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Check patient exists
            existing = _agent_instance.query(
                "SELECT id, first_name, last_name FROM patients WHERE id = :id",
                {"id": patient_id},
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Patient not found")

            # Build update data from non-None fields
            update_data = {}
            request_dict = request.model_dump(exclude_unset=True)

            for field, value in request_dict.items():
                if value is not None:
                    update_data[field] = value

            if not update_data:
                return {
                    "success": True,
                    "patient_id": patient_id,
                    "message": "No fields to update",
                    "updated_fields": [],
                }

            # Add updated_at timestamp
            update_data["updated_at"] = datetime.now().isoformat()

            # Use mixin's update() method
            _agent_instance.update(
                "patients",
                update_data,
                "id = :id",
                {"id": patient_id},
            )

            # Check if we need to update/create alerts based on changes
            if "allergies" in update_data and update_data["allergies"]:
                # Check for existing allergy alert
                existing_alert = _agent_instance.query(
                    """SELECT id FROM alerts
                       WHERE patient_id = :pid AND alert_type = 'allergy'
                       AND acknowledged = FALSE""",
                    {"pid": patient_id},
                )
                if not existing_alert:
                    _agent_instance.insert(
                        "alerts",
                        {
                            "patient_id": patient_id,
                            "alert_type": "allergy",
                            "priority": "critical",
                            "message": f"Patient has allergies: {update_data['allergies']}",
                            "data": json.dumps({"allergies": update_data["allergies"]}),
                        },
                    )

            # If phone was added, remove missing_field alert if it exists
            if "phone" in update_data and update_data["phone"]:
                _agent_instance.delete(
                    "alerts",
                    "patient_id = :pid AND alert_type = 'missing_field' "
                    "AND message LIKE '%phone%'",
                    {"pid": patient_id},
                )

            logger.info(f"Updated patient {patient_id}: {list(update_data.keys())}")

            return {
                "success": True,
                "patient_id": patient_id,
                "message": f"Updated {len(update_data)} field(s)",
                "updated_fields": list(update_data.keys()),
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating patient: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/patients/{patient_id}")
    async def delete_patient(patient_id: int, delete_file: bool = True):
        """Delete a patient, their associated data, and optionally the source file."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Check patient exists and get source file path
            existing = _agent_instance.query(
                "SELECT id, first_name, last_name, source_file FROM patients WHERE id = :id",
                {"id": patient_id},
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Patient not found")

            patient = existing[0]
            source_file = patient.get("source_file")
            file_deleted = False

            # Delete the source file if requested and it exists
            if delete_file and source_file:
                try:
                    source_path = Path(source_file)
                    if source_path.exists():
                        source_path.unlink()
                        file_deleted = True
                        logger.info(f"Deleted source file: {source_file}")
                except Exception as e:
                    logger.warning(f"Could not delete source file {source_file}: {e}")

            # Delete associated alerts first
            _agent_instance.delete("alerts", "patient_id = :id", {"id": patient_id})

            # Delete associated sessions
            _agent_instance.delete(
                "intake_sessions", "patient_id = :id", {"id": patient_id}
            )

            # Delete patient
            _agent_instance.delete("patients", "id = :id", {"id": patient_id})

            message = f"Deleted patient {patient['first_name']} {patient['last_name']}"
            if file_deleted:
                message += " and source file"

            return {
                "success": True,
                "message": message,
                "patient_id": patient_id,
                "file_deleted": file_deleted,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting patient: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/patients/{patient_id}/mark-reviewed")
    async def mark_patient_reviewed(patient_id: int) -> Dict[str, Any]:
        """
        Mark a patient's changes as reviewed, clearing the pending review status.

        This clears the changes_detected field in the most recent intake session
        for the specified patient.
        """
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Check patient exists
            existing = _agent_instance.query(
                "SELECT id, first_name, last_name FROM patients WHERE id = :id",
                {"id": patient_id},
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Patient not found")

            patient = existing[0]

            # Clear changes_detected in the most recent intake session
            latest_session = _agent_instance.query(
                """SELECT id FROM intake_sessions
                   WHERE patient_id = :pid
                   ORDER BY created_at DESC LIMIT 1""",
                {"pid": patient_id},
                one=True,
            )
            if latest_session:
                _agent_instance.update(
                    "intake_sessions",
                    {"changes_detected": None},
                    "id = :id",
                    {"id": latest_session["id"]},
                )

            logger.info(f"Marked patient {patient_id} as reviewed")

            return {
                "success": True,
                "patient_id": patient_id,
                "patient_name": f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
                "message": "Patient marked as reviewed",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error marking patient as reviewed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/patients/{patient_id}/file")
    async def download_patient_file(patient_id: int, inline: bool = False):
        """Download or view the original intake form file for a patient."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            results = _agent_instance.query(
                "SELECT file_content, source_file FROM patients WHERE id = :id",
                {"id": patient_id},
            )

            if not results:
                raise HTTPException(status_code=404, detail="Patient not found")

            patient = results[0]
            file_content = patient.get("file_content")
            source_file = patient.get("source_file", "intake_form")

            if not file_content:
                raise HTTPException(
                    status_code=404,
                    detail="Original file not available (older record)",
                )

            # Determine MIME type from filename
            filename = Path(source_file).name if source_file else "intake_form"
            suffix = Path(source_file).suffix.lower() if source_file else ""
            mime_types = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".tiff": "image/tiff",
                ".bmp": "image/bmp",
            }
            media_type = mime_types.get(suffix, "application/octet-stream")

            disposition = "inline" if inline else "attachment"
            return Response(
                content=file_content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'{disposition}; filename="{filename}"',
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/stats")
    async def get_stats() -> Dict[str, Any]:
        """Get processing statistics."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            return _agent_instance.get_stats()
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/alerts")
    async def list_alerts(
        unacknowledged_only: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """List alerts with optional filtering."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            if unacknowledged_only:
                results = _agent_instance.query(
                    """
                    SELECT a.*, p.first_name, p.last_name
                    FROM alerts a
                    LEFT JOIN patients p ON a.patient_id = p.id
                    WHERE a.acknowledged = FALSE
                    ORDER BY
                        CASE a.priority
                            WHEN 'critical' THEN 1
                            WHEN 'high' THEN 2
                            WHEN 'medium' THEN 3
                            ELSE 4
                        END,
                        a.created_at DESC
                    LIMIT :limit
                    """,
                    {"limit": limit},
                )
            else:
                results = _agent_instance.query(
                    """
                    SELECT a.*, p.first_name, p.last_name
                    FROM alerts a
                    LEFT JOIN patients p ON a.patient_id = p.id
                    ORDER BY a.created_at DESC
                    LIMIT :limit
                    """,
                    {"limit": limit},
                )

            return {"alerts": results, "count": len(results)}
        except Exception as e:
            logger.error(f"Error listing alerts: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/alerts/{alert_id}/acknowledge")
    async def acknowledge_alert(
        alert_id: int,
        acknowledged_by: str = "Staff",
    ) -> Dict[str, Any]:
        """Acknowledge an alert."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Check alert exists
            existing = _agent_instance.query(
                "SELECT id FROM alerts WHERE id = :id",
                {"id": alert_id},
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Alert not found")

            # Acknowledge using proper update method
            _agent_instance.update(
                "alerts",
                {
                    "acknowledged": True,
                    "acknowledged_by": acknowledged_by,
                    "acknowledged_at": datetime.now().isoformat(),
                },
                "id = :id",
                {"id": alert_id},
            )

            return {"success": True, "alert_id": alert_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error acknowledging alert: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50) -> Dict[str, Any]:
        """List recent intake sessions for audit trail."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            results = _agent_instance.query(
                """
                SELECT s.*, p.first_name, p.last_name
                FROM intake_sessions s
                LEFT JOIN patients p ON s.patient_id = p.id
                ORDER BY s.created_at DESC
                LIMIT :limit
                """,
                {"limit": limit},
            )
            return {"sessions": results, "count": len(results)}
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/events")
    async def event_stream():
        """SSE endpoint for real-time updates."""
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async with _sse_clients_lock:
            _sse_clients.append(client_queue)

        # Get recent events to replay to this new client
        with _recent_events_lock:
            events_to_replay = list(_recent_events)

        async def generate():
            """Generate SSE events."""
            last_heartbeat = time.time()

            try:
                # First, replay recent events to populate the feed for new clients
                for event in events_to_replay:
                    yield f"data: {_safe_json_dumps(event)}\n\n"

                while True:
                    try:
                        # Wait for event with timeout
                        try:
                            event = await asyncio.wait_for(
                                client_queue.get(), timeout=1.0
                            )
                            yield f"data: {_safe_json_dumps(event)}\n\n"
                        except asyncio.TimeoutError:
                            pass

                        # Send heartbeat every 30 seconds
                        current_time = time.time()
                        if current_time - last_heartbeat > 30:
                            yield f"data: {_safe_json_dumps({'type': 'heartbeat'})}\n\n"
                            last_heartbeat = current_time

                    except Exception as e:
                        logger.error(f"Error in SSE stream: {e}")
                        break
            finally:
                # Remove client on disconnect
                async with _sse_clients_lock:
                    if client_queue in _sse_clients:
                        _sse_clients.remove(client_queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/health")
    async def health_check() -> Dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "agent_running": _agent_instance is not None,
            "connected_clients": len(_sse_clients),
            "timestamp": datetime.now().isoformat(),
        }

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> Dict[str, Any]:
        """
        Chat with the agent using natural language.

        Send queries like:
        - "How many patients were processed today?"
        - "Find patient John Smith"
        - "Show me patients with allergies"
        - "What are the statistics?"
        """
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            # Process the query through the agent
            result = _agent_instance.process_query(request.message)

            # Extract the response text
            response_text = ""
            if isinstance(result, dict):
                response_text = result.get("result", str(result))
            else:
                response_text = str(result) if result else "No response generated."

            return {
                "success": True,
                "message": request.message,
                "response": response_text,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error processing chat: {e}")
            return {
                "success": False,
                "message": request.message,
                "response": f"Error processing your request: {str(e)}",
                "timestamp": datetime.now().isoformat(),
            }

    @app.get("/api/config")
    async def get_config() -> Dict[str, Any]:
        """Get current agent configuration with full resolved paths."""
        # Resolve full paths
        watch_path = Path(watch_dir).resolve()
        db_full_path = Path(db_path).resolve()

        if not _agent_instance:
            return {
                "watch_dir": str(watch_path),
                "watch_dir_relative": watch_dir,
                "db_path": str(db_full_path),
                "db_path_relative": db_path,
                "agent_running": False,
                "vlm_model": "Qwen3-VL-4B-Instruct-GGUF",
            }

        return {
            "watch_dir": str(Path(_agent_instance._watch_dir).resolve()),
            "watch_dir_relative": str(_agent_instance._watch_dir),
            "db_path": str(Path(_agent_instance._db_path).resolve()),
            "db_path_relative": str(_agent_instance._db_path),
            "agent_running": True,
            "vlm_model": _agent_instance._vlm_model,
        }

    @app.get("/api/init/status")
    async def get_init_status() -> Dict[str, Any]:
        """Check all required model initialization status with context size info."""
        REQUIRED_CONTEXT_SIZE = 32768

        # Required models for EMR agent
        vlm_model = "Qwen3-VL-4B-Instruct-GGUF"
        llm_model = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        embed_model = "nomic-embed-text-v2-moe-GGUF"

        try:
            from gaia.llm.lemonade_client import LemonadeClient

            client = LemonadeClient(model=vlm_model)

            # Check server health and context size
            try:
                health = client.health_check()
                server_running = health.get("status") == "ok"
                context_size = health.get("context_size", 0)
            except Exception:
                return {
                    "initialized": False,
                    "server_running": False,
                    "context_size": 0,
                    "context_size_ok": False,
                    "models": {
                        "vlm": {"name": vlm_model, "available": False, "loaded": False},
                        "llm": {"name": llm_model, "available": False, "loaded": False},
                        "embedding": {
                            "name": embed_model,
                            "available": False,
                            "loaded": False,
                        },
                    },
                    "ready_count": 0,
                    "total_models": 3,
                    "message": "Lemonade server not running",
                }

            # Check if models are available (downloaded)
            models_response = client.list_models()
            all_models = models_response.get("data", [])
            available_model_ids = [m.get("id", "") for m in all_models]

            vlm_available = vlm_model in available_model_ids
            llm_available = llm_model in available_model_ids
            embed_available = embed_model in available_model_ids

            # Check if models are loaded using check_model_loaded
            vlm_loaded = client.check_model_loaded(vlm_model)
            llm_loaded = client.check_model_loaded(llm_model)
            embed_loaded = client.check_model_loaded(embed_model)

            # Categorize all downloaded models for inventory
            vlm_models = []
            llm_models = []
            embed_models = []

            for m in all_models:
                model_id = m.get("id", "")
                model_lower = model_id.lower()
                if (
                    "vl" in model_lower
                    or "vision" in model_lower
                    or "vlm" in model_lower
                ):
                    vlm_models.append(model_id)
                elif (
                    "embed" in model_lower
                    or "bge" in model_lower
                    or "e5" in model_lower
                    or "nomic" in model_lower
                ):
                    embed_models.append(model_id)
                else:
                    llm_models.append(model_id)

            # Count ready models
            ready_count = sum([vlm_loaded, llm_loaded, embed_loaded])
            context_size_ok = context_size >= REQUIRED_CONTEXT_SIZE

            # Build status message
            if ready_count == 3 and context_size_ok:
                message = "All models ready"
            elif ready_count == 3:
                message = f"All models ready (context size: {context_size:,}, recommended: {REQUIRED_CONTEXT_SIZE:,})"
            elif ready_count > 0:
                message = f"{ready_count}/3 models ready"
            elif vlm_available or llm_available or embed_available:
                message = "Models not loaded"
            else:
                message = "Models not downloaded"

            return {
                "initialized": vlm_loaded,  # VLM is critical for form processing
                "server_running": server_running,
                "context_size": context_size,
                "context_size_ok": context_size_ok,
                "required_context_size": REQUIRED_CONTEXT_SIZE,
                "models": {
                    "vlm": {
                        "name": vlm_model,
                        "available": vlm_available,
                        "loaded": vlm_loaded,
                        "purpose": "Form extraction",
                    },
                    "llm": {
                        "name": llm_model,
                        "available": llm_available,
                        "loaded": llm_loaded,
                        "purpose": "Chat/query processing",
                    },
                    "embedding": {
                        "name": embed_model,
                        "available": embed_available,
                        "loaded": embed_loaded,
                        "purpose": "Similarity search",
                    },
                },
                "ready_count": ready_count,
                "total_models": 3,
                "model_inventory": {
                    "vlm": vlm_models[:3],
                    "llm": llm_models[:3],
                    "embedding": embed_models[:3],
                    "total": len(all_models),
                },
                "message": message,
            }
        except Exception as e:
            logger.error(f"Error checking init status: {e}")
            return {
                "initialized": False,
                "server_running": False,
                "context_size": 0,
                "context_size_ok": False,
                "models": {
                    "vlm": {"name": vlm_model, "available": False, "loaded": False},
                    "llm": {"name": llm_model, "available": False, "loaded": False},
                    "embedding": {
                        "name": embed_model,
                        "available": False,
                        "loaded": False,
                    },
                },
                "ready_count": 0,
                "total_models": 3,
                "message": f"Error: {str(e)}",
            }

    @app.post("/api/init")
    async def run_init() -> Dict[str, Any]:
        """
        Initialize all required models (VLM, LLM, Embedding).

        This runs the equivalent of `gaia-emr init` from the dashboard.
        """
        try:
            from gaia.llm.lemonade_client import LemonadeClient

            # Required models for EMR agent
            vlm_model = "Qwen3-VL-4B-Instruct-GGUF"
            llm_model = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
            embed_model = "nomic-embed-text-v2-moe-GGUF"

            required_models = [
                ("VLM", vlm_model),
                ("LLM", llm_model),
                ("Embedding", embed_model),
            ]

            steps = []

            # Step 1: Check server
            steps.append(
                {"step": 1, "name": "Checking Lemonade server", "status": "running"}
            )

            client = LemonadeClient(model=vlm_model)

            try:
                health = client.health_check()
                if health.get("status") != "ok":
                    return {
                        "success": False,
                        "message": "Lemonade server not healthy",
                        "steps": steps,
                    }
                steps[-1]["status"] = "complete"
            except Exception as e:
                steps[-1]["status"] = "error"
                return {
                    "success": False,
                    "message": f"Lemonade server not running: {str(e)}",
                    "steps": steps,
                }

            # Step 2: Load all models
            step_num = 2
            for model_type, model_name in required_models:
                steps.append(
                    {
                        "step": step_num,
                        "name": f"Loading {model_type}: {model_name}",
                        "status": "running",
                    }
                )

                try:
                    await asyncio.to_thread(
                        client.load_model,
                        model_name,
                        1800,
                        True,  # timeout, auto_download
                    )
                    steps[-1]["status"] = "complete"
                except Exception as e:
                    steps[-1]["status"] = "warning"
                    steps[-1]["error"] = str(e)[:50]

                step_num += 1

            # Verify models
            steps.append(
                {"step": step_num, "name": "Verifying models", "status": "running"}
            )

            vlm_ready = client.check_model_loaded(vlm_model)
            llm_ready = client.check_model_loaded(llm_model)
            embed_ready = client.check_model_loaded(embed_model)

            ready_count = sum([vlm_ready, llm_ready, embed_ready])
            steps[-1]["status"] = "complete"

            if vlm_ready:  # VLM is critical
                return {
                    "success": True,
                    "message": f"Initialized ({ready_count}/3 models ready)",
                    "ready_count": ready_count,
                    "steps": steps,
                }
            else:
                return {
                    "success": False,
                    "message": "VLM model failed to load - form processing will not work",
                    "ready_count": ready_count,
                    "steps": steps,
                }

        except Exception as e:
            logger.error(f"Error during init: {e}")
            return {
                "success": False,
                "message": f"Initialization failed: {str(e)}",
                "steps": steps if "steps" in dir() else [],
            }

    @app.get("/api/watch-folder")
    async def get_watch_folder_files() -> Dict[str, Any]:
        """
        Get list of files in the watch folder with their processing status.

        Returns files with status:
        - 'queued': File exists but hasn't been processed yet (orange dot)
        - 'processing': Currently being processed (flashing red dot)
        - 'processed': Successfully processed (green dot)
        """
        if not _agent_instance:
            return {
                "watch_dir": str(watch_dir) if watch_dir else "Not configured",
                "files": [],
                "total": 0,
                "processed_count": 0,
                "pending_count": 0,
            }

        try:
            from gaia.utils import compute_file_hash

            watch_path = Path(_agent_instance._watch_dir)

            if not watch_path.exists():
                return {
                    "watch_dir": str(watch_path),
                    "files": [],
                    "total": 0,
                    "processed_count": 0,
                    "pending_count": 0,
                    "error": "Watch folder does not exist",
                }

            # Get all file hashes from database to check processed status
            processed_hashes = {}
            try:
                db_results = _agent_instance.query(
                    "SELECT file_hash, id, first_name, last_name, created_at FROM patients WHERE file_hash IS NOT NULL"
                )
                for row in db_results:
                    processed_hashes[row["file_hash"]] = {
                        "patient_id": row["id"],
                        "patient_name": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                        "processed_at": row.get("created_at"),
                    }
            except Exception as e:
                logger.warning(f"Could not query processed hashes: {e}")

            # Get current processing file
            with _processing_lock:
                current_file = _current_processing_file

            # Get failed file hashes
            with _failed_lock:
                failed_hashes = set(_failed_file_hashes)

            # Supported file extensions
            supported_extensions = {".jpg", ".jpeg", ".png", ".pdf", ".tiff", ".bmp"}

            files = []
            processed_count = 0
            queued_count = 0
            processing_count = 0
            failed_count = 0

            for file_path in watch_path.iterdir():
                if not file_path.is_file():
                    continue

                suffix = file_path.suffix.lower()
                if suffix not in supported_extensions:
                    continue

                try:
                    stat = file_path.stat()
                    file_hash = compute_file_hash(str(file_path))

                    # Determine status
                    if file_path.name == current_file:
                        status = "processing"
                        processing_count += 1
                    elif file_hash and file_hash in processed_hashes:
                        status = "processed"
                        processed_count += 1
                    elif file_hash and file_hash in failed_hashes:
                        status = "failed"
                        failed_count += 1
                    else:
                        status = "queued"
                        queued_count += 1

                    file_info = {
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": stat.st_size,
                        "size_formatted": _format_file_size(stat.st_size),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "modified_formatted": datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime("%Y-%m-%d %H:%M"),
                        "extension": suffix,
                        "status": status,
                        "hash": file_hash[:12] + "..." if file_hash else None,
                    }

                    # Add patient info if processed
                    if status == "processed" and file_hash in processed_hashes:
                        file_info["patient_id"] = processed_hashes[file_hash][
                            "patient_id"
                        ]
                        file_info["patient_name"] = processed_hashes[file_hash][
                            "patient_name"
                        ]
                        file_info["processed_at"] = processed_hashes[file_hash][
                            "processed_at"
                        ]

                    files.append(file_info)

                except (OSError, IOError) as e:
                    logger.warning(f"Could not read file {file_path}: {e}")
                    continue

            # Sort files: processing first, then failed, then queued, then processed
            status_order = {"processing": 0, "failed": 1, "queued": 2, "processed": 3}
            files.sort(
                key=lambda f: (status_order.get(f["status"], 4), f.get("modified", ""))
            )

            return {
                "watch_dir": str(watch_path),
                "files": files,
                "total": len(files),
                "processed_count": processed_count,
                "queued_count": queued_count,
                "processing_count": processing_count,
                "failed_count": failed_count,
                "pending_count": queued_count + processing_count,  # backwards compat
                "current_processing": current_file,
            }

        except Exception as e:
            logger.error(f"Error getting watch folder files: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _format_file_size(size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    @app.put("/api/config/watch-dir")
    async def update_watch_dir(config: WatchDirConfig) -> Dict[str, Any]:
        """Update the watch directory."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        new_dir = Path(config.watch_dir).expanduser().resolve()

        try:
            # Create directory if it doesn't exist
            new_dir.mkdir(parents=True, exist_ok=True)

            # Stop existing watchers
            _agent_instance.stop_all_watchers()

            # Update watch directory
            _agent_instance._watch_dir = new_dir

            # Restart file watching
            _agent_instance._start_file_watching()

            logger.info(f"Watch directory updated to: {new_dir}")

            return {
                "success": True,
                "watch_dir": str(new_dir),
                "message": f"Now watching: {new_dir}",
            }
        except Exception as e:
            logger.error(f"Failed to update watch directory: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/upload")
    async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
        """Upload and process an intake form file."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        # Validate filename
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        # Validate file type
        allowed_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp"}
        suffix = Path(file.filename).suffix.lower()

        if suffix not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}. Allowed: {', '.join(allowed_extensions)}",
            )

        try:
            # Read file content
            content = await file.read()

            if not content:
                raise HTTPException(status_code=400, detail="Empty file uploaded")

            # Sanitize filename (remove path components, keep only the basename)
            safe_filename = Path(file.filename).name

            # Ensure watch directory exists
            _agent_instance._watch_dir.mkdir(parents=True, exist_ok=True)

            # Add to API processing set BEFORE saving file to prevent race condition
            # with file watcher detecting the file before we add it to the set
            with _api_processing_lock:
                _api_processing_files.add(safe_filename)

            # Save file to watch directory
            file_path = _agent_instance._watch_dir / safe_filename

            with open(file_path, "wb") as f:
                f.write(content)

            logger.info(f"File uploaded: {file_path} ({len(content)} bytes)")

            # Check if file is a duplicate before processing
            from gaia.utils import compute_file_hash

            file_hash = compute_file_hash(str(file_path))
            if file_hash:
                existing = _agent_instance.query(
                    "SELECT id, first_name, last_name FROM patients WHERE file_hash = ?",
                    (file_hash,),
                )
                if existing:
                    # Clean up from set since we're returning early
                    with _api_processing_lock:
                        _api_processing_files.discard(safe_filename)
                    patient = existing[0]
                    return {
                        "success": True,
                        "filename": safe_filename,
                        "patient_id": patient.get("id"),
                        "patient_name": f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
                        "is_duplicate": True,
                        "message": "File already processed - showing existing patient",
                    }

            def process_with_flag(fp):
                """Process file with thread-local flag to mark as API call."""
                _thread_local.is_api_call = True
                try:
                    return _agent_instance._process_intake_form(fp)
                finally:
                    _thread_local.is_api_call = False

            try:
                # Process the file in a thread pool to avoid blocking the event loop
                # This allows SSE events to be sent in real-time during processing
                result = await asyncio.to_thread(process_with_flag, str(file_path))
            finally:
                # Remove from API processing set
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)

            if result:
                return {
                    "success": True,
                    "filename": safe_filename,
                    "patient_id": result.get("id"),
                    "patient_name": f"{result.get('first_name', '')} {result.get('last_name', '')}".strip(),
                    "is_new_patient": result.get("is_new_patient", True),
                    "message": "File processed successfully",
                }
            else:
                return {
                    "success": False,
                    "filename": safe_filename,
                    "message": "Extraction failed - check if form is filled out correctly",
                }
        except HTTPException:
            # Clean up from set on error (safe_filename may not be defined if early error)
            try:
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)
            except NameError:
                pass
            raise
        except Exception as e:
            # Clean up from set on error (safe_filename may not be defined if early error)
            try:
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)
            except NameError:
                pass
            logger.error(f"Error uploading file: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/upload-path")
    async def upload_file_by_path(request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a file by path (for Electron drag-drop support)."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        file_path = request.get("file_path")
        if not file_path:
            raise HTTPException(status_code=400, detail="No file_path provided")

        source_path = Path(file_path)

        if not source_path.exists():
            raise HTTPException(status_code=400, detail=f"File not found: {file_path}")

        # Validate file type
        allowed_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp"}
        suffix = source_path.suffix.lower()

        if suffix not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}. Allowed: {', '.join(allowed_extensions)}",
            )

        try:
            import shutil

            # Ensure watch directory exists
            _agent_instance._watch_dir.mkdir(parents=True, exist_ok=True)

            # Add to API processing set BEFORE copying file to prevent race condition
            # with file watcher detecting the file before we add it to the set
            safe_filename = source_path.name
            with _api_processing_lock:
                _api_processing_files.add(safe_filename)

            # Copy file to watch directory
            dest_path = _agent_instance._watch_dir / source_path.name

            # Only copy if source is not already in watch directory
            if source_path.parent.resolve() != _agent_instance._watch_dir.resolve():
                shutil.copy2(source_path, dest_path)
                logger.info(f"File copied to watch dir: {dest_path}")
            else:
                dest_path = source_path
                logger.info(f"File already in watch dir: {dest_path}")

            # Check if file is a duplicate before processing
            from gaia.utils import compute_file_hash

            file_hash = compute_file_hash(str(dest_path))
            if file_hash:
                existing = _agent_instance.query(
                    "SELECT id, first_name, last_name FROM patients WHERE file_hash = ?",
                    (file_hash,),
                )
                if existing:
                    # Clean up from set since we're returning early
                    with _api_processing_lock:
                        _api_processing_files.discard(safe_filename)
                    patient = existing[0]
                    return {
                        "success": True,
                        "filename": source_path.name,
                        "patient_id": patient.get("id"),
                        "patient_name": f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
                        "is_duplicate": True,
                        "message": "File already processed - showing existing patient",
                    }

            def process_with_flag(fp):
                """Process file with thread-local flag to mark as API call."""
                _thread_local.is_api_call = True
                try:
                    return _agent_instance._process_intake_form(fp)
                finally:
                    _thread_local.is_api_call = False

            try:
                # Process the file in a thread pool to avoid blocking the event loop
                # This allows SSE events to be sent in real-time during processing
                result = await asyncio.to_thread(process_with_flag, str(dest_path))
            finally:
                # Remove from API processing set
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)

            if result:
                return {
                    "success": True,
                    "filename": source_path.name,
                    "patient_id": result.get("id"),
                    "patient_name": f"{result.get('first_name', '')} {result.get('last_name', '')}".strip(),
                    "is_new_patient": result.get("is_new_patient", True),
                    "message": "File processed successfully",
                }
            else:
                return {
                    "success": False,
                    "filename": source_path.name,
                    "message": "Extraction failed - check if form is filled out correctly",
                }
        except HTTPException:
            # Clean up from set on error
            try:
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)
            except NameError:
                pass
            raise
        except Exception as e:
            # Clean up from set on error
            try:
                with _api_processing_lock:
                    _api_processing_files.discard(safe_filename)
            except NameError:
                pass
            logger.error(f"Error processing file by path: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/database")
    async def clear_database() -> Dict[str, Any]:
        """Clear all data from the database and reset statistics."""
        if not _agent_instance:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        try:
            result = _agent_instance.clear_database()

            if result.get("success"):
                # Clear the recent events buffer
                with _recent_events_lock:
                    _recent_events.clear()

                # Broadcast database cleared event to all SSE clients
                event = {
                    "type": "database_cleared",
                    "data": result.get("deleted", {}),
                    "timestamp": datetime.now().isoformat(),
                }
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.run_coroutine_threadsafe(broadcast_event(event), loop)
                except RuntimeError:
                    pass

                logger.info(
                    f"Database cleared: {result.get('deleted', {}).get('patients', 0)} patients"
                )
                return result
            else:
                raise HTTPException(
                    status_code=500,
                    detail=result.get("error", "Failed to clear database"),
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error clearing database: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Serve static frontend files
    dashboard_dir = Path(__file__).parent / "frontend" / "dist"
    if dashboard_dir.exists():
        app.mount(
            "/", StaticFiles(directory=str(dashboard_dir), html=True), name="static"
        )

        @app.get("/")
        async def serve_index():
            """Serve index.html."""
            return FileResponse(dashboard_dir / "index.html")

    else:

        @app.get("/")
        async def no_frontend():
            """Placeholder when frontend not built."""
            return {
                "message": "EMR Dashboard API is running",
                "frontend": "not built (run npm build in dashboard/frontend)",
                "api_docs": "/docs",
            }

    return app


def run_dashboard(
    watch_dir: str = "./intake_forms",
    db_path: str = "./data/patients.db",
    host: str = "127.0.0.1",
    port: int = 8080,
):
    """
    Run the EMR dashboard server.

    Args:
        watch_dir: Directory to watch for intake forms
        db_path: Path to patient database
        host: Server host (default: 127.0.0.1)
        port: Server port (default: 8080)
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Install with: pip install 'amd-gaia[api]'"
        )

    app = create_app(watch_dir=watch_dir, db_path=db_path)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",  # Suppress INFO-level request logs
        access_log=False,  # Disable access logs (GET/POST endpoint logs)
    )
