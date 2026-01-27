# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI for Medical Intake Agent."""

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gaia.agents.emr.agent import MedicalIntakeAgent

logger = logging.getLogger(__name__)
console = Console()


def _print_header(watch_dir: str, db_path: str):
    """Print a styled header for the CLI."""
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Medical Intake Agent[/bold cyan]\n"
            "[dim]Automatic Patient Form Processing[/dim]",
            border_style="cyan",
        )
    )

    # Status table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("📁 Watch folder:", watch_dir)
    table.add_row("💾 Database:", db_path)
    console.print(table)
    console.print()

    # Commands help
    console.print("[dim]Commands:[/dim]")
    console.print("  [cyan]stats[/cyan]      Show processing statistics")
    console.print("  [cyan]quit[/cyan]       Stop and exit")
    console.print("  [dim]Or type questions about patients[/dim]")
    console.print()


def _print_prompt():
    """Print the input prompt with visual separators."""
    console.print("─" * 80, style="dim")
    console.print("> ", end="", style="bold green")
    sys.stdout.flush()  # Ensure prompt is displayed before input() blocks


def cmd_watch(args):
    """Start watching directory for intake forms."""
    _print_header(args.watch_dir, args.db)

    console.print("[dim]Starting agent...[/dim]")

    agent = MedicalIntakeAgent(
        watch_dir=args.watch_dir,
        db_path=args.db,
        vlm_model=args.vlm_model,
    )

    console.print("[green]✓ Ready![/green] Drop intake forms to process them.\n")
    sys.stdout.flush()  # Ensure Ready message appears before prompt

    try:
        while True:
            try:
                _print_prompt()
                user_input = input().strip()
            except EOFError:
                break

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                break

            console.print("─" * 80, style="dim")

            if user_input.lower() == "stats":
                cmd_stats_inline(agent)
            else:
                # Process the query
                agent.process_query(user_input)
            print()

    except KeyboardInterrupt:
        print()
    finally:
        console.print("[dim]Stopping agent...[/dim]")
        agent.stop()
        console.print("[green]✓ Stopped[/green]")


def cmd_stats_inline(agent):
    """Show stats inline during watch mode."""
    try:
        stats = agent.get_stats()
        _print_stats_table(stats)
    except Exception as e:
        console.print(f"[red]Error getting stats: {e}[/red]")


def cmd_process(args):
    """Process a single intake form file."""
    if not Path(args.file).exists():
        console.print(f"[red]Error: File not found: {args.file}[/red]")
        return 1

    console.print(f"[dim]Processing: {args.file}[/dim]")

    agent = MedicalIntakeAgent(
        watch_dir=args.watch_dir,
        db_path=args.db,
        vlm_model=args.vlm_model,
        auto_start_watching=False,
    )

    try:
        # pylint: disable=protected-access
        patient_data = agent._process_intake_form(args.file)

        if patient_data:
            # Agent already prints success and patient details
            return 0
        else:
            console.print(f"[red]Failed to process: {args.file}[/red]")
            return 1

    finally:
        agent.stop()


def cmd_query(args):
    """Query patient database."""
    agent = MedicalIntakeAgent(
        watch_dir=args.watch_dir,
        db_path=args.db,
        auto_start_watching=False,
    )

    try:
        agent.process_query(args.question)
        return 0
    finally:
        agent.stop()


def _print_stats_table(stats: dict):
    """Print statistics using Rich formatting."""
    console.print()

    # Time savings highlight
    time_table = Table(show_header=False, box=None, padding=(0, 1))
    time_table.add_column(style="bold green")
    time_table.add_column(style="green")
    time_table.add_row(
        f"⏱️  {stats['time_saved_minutes']} min saved",
        f"({stats['time_saved_percent']} faster)",
    )
    console.print(Panel(time_table, title="Time Savings", border_style="green"))

    # Main stats grid
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column()
    grid.add_column()

    # Patients table
    patients = Table(show_header=False, box=None)
    patients.add_column(style="dim")
    patients.add_column(style="bold")
    patients.add_row("Total", str(stats["total_patients"]))
    patients.add_row("New", str(stats["new_patients"]))
    patients.add_row("Returning", str(stats["returning_patients"]))
    patients.add_row("Today", str(stats["processed_today"]))

    # Processing table
    processing = Table(show_header=False, box=None)
    processing.add_column(style="dim")
    processing.add_column(style="bold")
    processing.add_row("Processed", str(stats["files_processed"]))
    processing.add_row("Success", str(stats["extraction_success"]))
    processing.add_row("Failed", str(stats["extraction_failed"]))
    processing.add_row("Rate", stats["success_rate"])

    grid.add_row(
        Panel(patients, title="👥 Patients", border_style="cyan"),
        Panel(processing, title="📋 Processing", border_style="cyan"),
    )
    console.print(grid)

    # Alerts (if any)
    if stats.get("unacknowledged_alerts", 0) > 0:
        console.print(
            f"[bold red]🚨 {stats['unacknowledged_alerts']} unacknowledged alert(s)[/bold red]"
        )
    console.print()


def cmd_stats(args):
    """Show processing statistics."""
    agent = MedicalIntakeAgent(
        watch_dir=args.watch_dir,
        db_path=args.db,
        auto_start_watching=False,
        silent_mode=True,
    )

    try:
        stats = agent.get_stats()
        _print_stats_table(stats)
        return 0
    finally:
        agent.stop()


def cmd_reset(args):
    """Reset by deleting the database file."""
    import os

    from rich.prompt import Confirm

    db_path = Path(args.db)

    # Check if database exists
    if not db_path.exists():
        console.print("[dim]Database file does not exist. Nothing to reset.[/dim]")
        return 0

    # Get stats before deletion to show what will be deleted
    total_patients = 0
    agent = None
    try:
        agent = MedicalIntakeAgent(
            watch_dir=args.watch_dir,
            db_path=args.db,
            auto_start_watching=False,
            silent_mode=True,
        )
        stats = agent.get_stats()
        total_patients = stats.get("total_patients", 0)
    except Exception:
        pass  # If we can't read stats, proceed anyway
    finally:
        if agent:
            agent.stop()

    # Confirmation prompt unless --force is used
    if not args.force:
        console.print()
        console.print(
            "[bold yellow]⚠️  WARNING:[/bold yellow] This will permanently delete:"
        )
        if total_patients > 0:
            console.print(f"  • {total_patients} patient record(s)")
            console.print("  • All associated alerts and intake sessions")
        console.print(f"  • Database file: {db_path}")
        console.print()

        if not Confirm.ask("[bold red]Are you sure you want to continue?[/bold red]"):
            console.print("[dim]Cancelled.[/dim]")
            return 0

    # Delete the database file
    try:
        os.remove(db_path)
        console.print()
        console.print("[bold green]✓ Database deleted successfully[/bold green]")
        console.print(f"  Removed: {db_path}")
        console.print()
        console.print(
            "[dim]A fresh database will be created when you next run the agent.[/dim]"
        )
        return 0
    except Exception as e:
        console.print(f"[red]Error deleting database: {e}[/red]")
        return 1


def cmd_init(args):
    """Initialize EMR agent by downloading and loading required models."""
    import time

    from gaia.llm.lemonade_client import LemonadeClient

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]EMR Agent Setup[/bold cyan]\n"
            "[dim]Downloading and loading required models[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    # Required models for EMR agent
    vlm_model = args.vlm_model  # Default: Qwen3-VL-4B-Instruct-GGUF
    llm_model = "Qwen3-Coder-30B-A3B-Instruct-GGUF"  # For chat/query processing
    embed_model = "nomic-embed-text-v2-moe-GGUF"  # For similarity search

    REQUIRED_CONTEXT_SIZE = 32768

    # Step 1: Check Lemonade server and context size
    console.print("[bold]Step 1:[/bold] Checking Lemonade server...")
    try:
        client = LemonadeClient(model=vlm_model)
        health = client.health_check()
        if health.get("status") == "ok":
            console.print("  [green]✓[/green] Lemonade server is running")

            # Check context size
            context_size = health.get("context_size", 0)
            if context_size >= REQUIRED_CONTEXT_SIZE:
                console.print(
                    f"  [green]✓[/green] Context size: [cyan]{context_size:,}[/cyan] tokens (recommended: {REQUIRED_CONTEXT_SIZE:,})"
                )
            elif context_size > 0:
                console.print(
                    f"  [yellow]⚠[/yellow] Context size: [yellow]{context_size:,}[/yellow] tokens"
                )
                console.print(
                    f"    [yellow]Warning:[/yellow] Context size should be at least [cyan]{REQUIRED_CONTEXT_SIZE:,}[/cyan] for reliable form processing"
                )
                console.print(
                    "    [dim]To fix: Right-click Lemonade tray → Settings → Context Size → 32768[/dim]"
                )
            else:
                console.print(
                    "  [dim]Context size: Not reported (will check after model load)[/dim]"
                )
        else:
            console.print("  [red]✗[/red] Lemonade server not responding")
            console.print()
            console.print("[yellow]Please start Lemonade server first:[/yellow]")
            console.print("  1. Open Lemonade from the system tray")
            console.print("  2. Or run: [cyan]lemonade-server[/cyan]")
            return 1
    except Exception as e:
        console.print(f"  [red]✗[/red] Cannot connect to Lemonade: {e}")
        console.print()
        console.print("[yellow]Please start Lemonade server first:[/yellow]")
        console.print("  1. Open Lemonade from the system tray")
        console.print("  2. Or run: [cyan]lemonade-server[/cyan]")
        return 1

    # Step 2: Check required models
    console.print()
    console.print("[bold]Step 2:[/bold] Checking required models...")

    try:
        models_response = client.list_models()
        available_models = models_response.get("data", [])
        downloaded_model_ids = [m.get("id", "") for m in available_models]

        # Check each required model
        required_models = [
            ("VLM", vlm_model, "Form extraction"),
            ("LLM", llm_model, "Chat/query processing"),
            ("Embedding", embed_model, "Similarity search"),
        ]

        models_to_download = []
        for model_type, model_name, _purpose in required_models:
            is_downloaded = model_name in downloaded_model_ids
            if is_downloaded:
                console.print(
                    f"  [green]✓[/green] {model_type}: [cyan]{model_name}[/cyan]"
                )
            else:
                console.print(
                    f"  [dim]○[/dim] {model_type}: [cyan]{model_name}[/cyan] [dim](not downloaded)[/dim]"
                )
                models_to_download.append((model_type, model_name))

        if models_to_download:
            console.print()
            console.print(
                f"  [yellow]⚠️  {len(models_to_download)} model(s) need to be downloaded[/yellow]"
            )

    except Exception as e:
        console.print(f"  [red]✗[/red] Failed to check models: {e}")
        return 1

    # Step 3: Load all required models
    console.print()
    console.print("[bold]Step 3:[/bold] Loading required models...")
    console.print("  [dim]Loading models into memory for fast inference...[/dim]")
    console.print()

    models_loaded = {}

    # Load VLM model first (most important for form processing)
    for model_type, model_name in [
        ("VLM", vlm_model),
        ("LLM", llm_model),
        ("Embedding", embed_model),
    ]:
        console.print(f"  Loading {model_type}: [cyan]{model_name}[/cyan]...")

        try:
            start_time = time.time()
            client.load_model(model_name, timeout=1800, auto_download=True)
            elapsed = time.time() - start_time
            models_loaded[model_type] = True
            console.print(f"  [green]✓[/green] {model_type} loaded ({elapsed:.1f}s)")
        except Exception as e:
            error_msg = str(e)
            models_loaded[model_type] = False

            # Check for common errors
            if "being used by another process" in error_msg:
                console.print(
                    f"  [yellow]![/yellow] {model_type}: File locked, try again later"
                )
            elif (
                "not found" in error_msg.lower()
                or "does not exist" in error_msg.lower()
            ):
                console.print(
                    f"  [yellow]![/yellow] {model_type}: Model not available in registry"
                )
            else:
                console.print(f"  [yellow]![/yellow] {model_type}: {error_msg[:50]}...")

    # Check if critical models loaded
    if not models_loaded.get("VLM"):
        console.print()
        console.print(
            "[red]✗ VLM model failed to load - form processing will not work[/red]"
        )
        return 1

    # Clear VLM context to ensure fresh memory allocation
    console.print()
    console.print("  [dim]Clearing VLM context for clean memory...[/dim]")
    try:
        client.unload_model()
        client.load_model(vlm_model, timeout=300, auto_download=True)
        console.print("  [green]✓[/green] VLM context cleared")
    except Exception as e:
        console.print(f"  [dim]Context clear skipped: {e}[/dim]")

    # Step 4: Verify models and check context size
    console.print()
    console.print("[bold]Step 4:[/bold] Verifying models are ready...")

    vlm_ready = False
    llm_ready = False
    embed_ready = False
    final_context_size = 0

    try:
        # Check health for context size
        health = client.health_check()
        final_context_size = health.get("context_size", 0)

        # Check each model
        vlm_ready = client.check_model_loaded(vlm_model)
        llm_ready = client.check_model_loaded(llm_model)
        embed_ready = client.check_model_loaded(embed_model)

        if vlm_ready:
            console.print("  [green]✓[/green] VLM: Ready for form extraction")
        else:
            console.print("  [yellow]![/yellow] VLM: Will load on first use")

        if llm_ready:
            console.print("  [green]✓[/green] LLM: Ready for chat queries")
        else:
            console.print("  [dim]○[/dim] LLM: Will load on first use")

        if embed_ready:
            console.print("  [green]✓[/green] Embedding: Ready for search")
        else:
            console.print("  [dim]○[/dim] Embedding: Will load on first use")

        # Report context size
        if final_context_size >= REQUIRED_CONTEXT_SIZE:
            console.print(
                f"  [green]✓[/green] Context size: [cyan]{final_context_size:,}[/cyan] tokens"
            )
        elif final_context_size > 0:
            console.print(
                f"  [yellow]⚠[/yellow] Context size: [yellow]{final_context_size:,}[/yellow] tokens (need {REQUIRED_CONTEXT_SIZE:,})"
            )

    except Exception as e:
        console.print(f"  [yellow]![/yellow] Could not verify: {e}")

    # Step 5: Show all downloaded and loaded models
    console.print()
    console.print("[bold]Step 5:[/bold] Model inventory...")

    try:
        models_response = client.list_models()
        all_models = models_response.get("data", [])

        # Categorize models
        vlm_models = []
        llm_models = []
        embed_models = []

        for m in all_models:
            model_id = m.get("id", "")
            model_lower = model_id.lower()

            if "vl" in model_lower or "vision" in model_lower or "vlm" in model_lower:
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

        # Show categorized models
        if vlm_models:
            console.print(
                f"  [cyan]VLM Models:[/cyan] {', '.join(vlm_models[:3])}"
                + (f" (+{len(vlm_models)-3} more)" if len(vlm_models) > 3 else "")
            )
        if llm_models:
            console.print(
                f"  [cyan]LLM Models:[/cyan] {', '.join(llm_models[:3])}"
                + (f" (+{len(llm_models)-3} more)" if len(llm_models) > 3 else "")
            )
        if embed_models:
            console.print(
                f"  [cyan]Embedding Models:[/cyan] {', '.join(embed_models[:3])}"
                + (f" (+{len(embed_models)-3} more)" if len(embed_models) > 3 else "")
            )

        console.print(f"  [dim]Total models available: {len(all_models)}[/dim]")

    except Exception as e:
        console.print(f"  [dim]Could not list models: {e}[/dim]")

    # Success summary
    console.print()

    # Build model status lines
    model_status_lines = []

    # VLM status
    if vlm_ready:
        model_status_lines.append(
            f"[green]✓[/green] VLM: [cyan]{vlm_model}[/cyan] - Ready"
        )
    else:
        model_status_lines.append(
            f"[yellow]![/yellow] VLM: [cyan]{vlm_model}[/cyan] - Will load on first use"
        )

    # LLM status
    if llm_ready:
        model_status_lines.append(
            f"[green]✓[/green] LLM: [cyan]{llm_model}[/cyan] - Ready"
        )
    else:
        model_status_lines.append(
            f"[dim]○[/dim] LLM: [cyan]{llm_model}[/cyan] - Will load on first use"
        )

    # Embedding status
    if embed_ready:
        model_status_lines.append(
            f"[green]✓[/green] Embedding: [cyan]{embed_model}[/cyan] - Ready"
        )
    else:
        model_status_lines.append(
            f"[dim]○[/dim] Embedding: [cyan]{embed_model}[/cyan] - Will load on first use"
        )

    # Context size status
    if final_context_size >= REQUIRED_CONTEXT_SIZE:
        model_status_lines.append(
            f"[green]✓[/green] Context size: {final_context_size:,} tokens"
        )
    elif final_context_size > 0:
        model_status_lines.append(
            f"[yellow]⚠[/yellow] Context size: {final_context_size:,} tokens (need {REQUIRED_CONTEXT_SIZE:,})"
        )

    # Count ready models
    ready_count = sum([vlm_ready, llm_ready, embed_ready])

    console.print(
        Panel.fit(
            f"[bold green]✓ EMR Agent initialized ({ready_count}/3 models ready)[/bold green]\n\n"
            + "\n".join(model_status_lines)
            + "\n\n"
            "[dim]You can now run:[/dim]\n"
            "  [cyan]gaia-emr dashboard[/cyan]  - Start the web dashboard\n"
            "  [cyan]gaia-emr watch[/cyan]      - Watch folder for intake forms\n"
            "  [cyan]gaia-emr process <file>[/cyan] - Process a single file",
            border_style="green",
        )
    )
    console.print()

    # Context size warning if needed
    if 0 < final_context_size < REQUIRED_CONTEXT_SIZE:
        console.print(
            Panel.fit(
                "[yellow]⚠️  Context Size Warning[/yellow]\n\n"
                f"Current context size ({final_context_size:,}) may be too small for processing intake forms.\n"
                "Large images can require 4,000-8,000+ tokens.\n\n"
                "[bold]To fix:[/bold]\n"
                "  1. Right-click Lemonade tray icon → Settings\n"
                "  2. Set Context Size to [cyan]32768[/cyan]\n"
                "  3. Click Apply and restart the model",
                border_style="yellow",
            )
        )
        console.print()

    return 0


def cmd_test(args):
    """Test VLM extraction pipeline on a single file."""
    import io
    import json
    import time

    from PIL import Image

    from gaia.llm import VLMClient

    file_path = Path(args.file)
    if not file_path.exists():
        console.print(f"[red]Error: File not found: {file_path}[/red]")
        return 1

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]EMR Agent - VLM Pipeline Test[/bold cyan]\n"
            f"[dim]Testing extraction on: {file_path.name}[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    # Step 1: Read and analyze file
    console.print("[bold]Step 1:[/bold] Reading file...")
    try:
        raw_bytes = file_path.read_bytes()
        file_size_kb = len(raw_bytes) / 1024
        console.print(f"  File size: {file_size_kb:.1f} KB")

        # Get image dimensions
        img = Image.open(io.BytesIO(raw_bytes))
        orig_width, orig_height = img.size
        console.print(f"  Dimensions: {orig_width}x{orig_height} pixels")

        # Auto-rotate based on EXIF orientation
        try:
            from PIL import ExifTags

            exif = img._getexif()  # pylint: disable=protected-access
            if exif:
                for tag, value in exif.items():
                    if ExifTags.TAGS.get(tag) == "Orientation":
                        if value == 3:
                            img = img.rotate(180, expand=True)
                            console.print("  [dim]Auto-rotated 180°[/dim]")
                        elif value == 6:
                            img = img.rotate(270, expand=True)
                            console.print("  [dim]Auto-rotated 90° CW[/dim]")
                        elif value == 8:
                            img = img.rotate(90, expand=True)
                            console.print("  [dim]Auto-rotated 90° CCW[/dim]")
                        orig_width, orig_height = img.size
                        break
        except Exception:
            pass  # No EXIF or rotation info
    except Exception as e:
        console.print(f"  [red]✗[/red] Failed to read file: {e}")
        return 1

    # Step 2: Optimize image (same as agent)
    console.print()
    console.print("[bold]Step 2:[/bold] Optimizing image...")
    max_dimension = args.max_dimension
    jpeg_quality = args.jpeg_quality

    try:
        if orig_width > max_dimension or orig_height > max_dimension:
            scale = min(max_dimension / orig_width, max_dimension / orig_height)
            new_width = int(orig_width * scale)
            new_height = int(orig_height * scale)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            console.print(
                f"  Resized: {orig_width}x{orig_height} → {new_width}x{new_height}"
            )
        else:
            new_width, new_height = orig_width, orig_height
            console.print(f"  No resize needed (under {max_dimension}px)")

        # Convert to RGB and JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
        image_bytes = output.getvalue()

        opt_size_kb = len(image_bytes) / 1024
        reduction = (1 - opt_size_kb / file_size_kb) * 100
        console.print(
            f"  Optimized: {file_size_kb:.0f}KB → {opt_size_kb:.0f}KB ({reduction:.0f}% smaller)"
        )

        # Estimate image tokens (rough: ~1 token per 14x14 pixel patch)
        est_img_tokens = (new_width // 14) * (new_height // 14)
        console.print(f"  Est. image tokens: ~{est_img_tokens:,}")
    except Exception as e:
        console.print(f"  [red]✗[/red] Failed to optimize: {e}")
        return 1

    # Step 3: Initialize VLM
    console.print()
    console.print("[bold]Step 3:[/bold] Initializing VLM...")
    try:
        vlm = VLMClient(vlm_model=args.vlm_model)

        # Clear context if requested (unload and reload model)
        if getattr(args, "clear_context", False):
            console.print("  [dim]Clearing VLM context (unload + reload)...[/dim]")
            try:
                vlm.client.unload_model()
                vlm.client.load_model(args.vlm_model, timeout=300, auto_download=True)
                console.print("  [green]✓[/green] Context cleared")
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow] Could not clear context: {e}")

        console.print(f"  [green]✓[/green] VLM ready: [cyan]{vlm.vlm_model}[/cyan]")
    except Exception as e:
        console.print(f"  [red]✗[/red] Failed to initialize VLM: {e}")
        return 1

    # Step 4: Extract data with auto-retry on memory errors
    console.print()
    console.print("[bold]Step 4:[/bold] Extracting patient data...")

    extraction_prompt = """Extract ALL patient information from this medical intake form.

Return a JSON object with these fields (use null for missing/unclear):
{
    "form_date": "YYYY-MM-DD (date form was filled, today's date)",
    "first_name": "...",
    "last_name": "...",
    "date_of_birth": "YYYY-MM-DD",
    "age": "patient's age if listed",
    "gender": "Male/Female/Other",
    "preferred_pronouns": "he/him, she/her, they/them if listed",
    "ssn": "XXX-XX-XXXX (social security number)",
    "marital_status": "Single/Married/Divorced/Widowed/Partnered",
    "spouse_name": "spouse's name if listed",
    "phone": "home phone number",
    "mobile_phone": "cell/mobile phone number",
    "work_phone": "work phone number",
    "email": "...",
    "address": "street address",
    "city": "...",
    "state": "...",
    "zip_code": "...",
    "preferred_language": "English/Spanish/etc if listed",
    "race": "if listed",
    "ethnicity": "if listed",
    "contact_preference": "preferred contact method if listed",
    "emergency_contact_name": "name of emergency contact person",
    "emergency_contact_relationship": "relationship to patient (e.g. Mom, Spouse, Friend)",
    "emergency_contact_phone": "emergency contact's phone number",
    "referring_physician": "name of referring physician/doctor",
    "referring_physician_phone": "phone number next to referring physician",
    "primary_care_physician": "PCP name if different from referring",
    "preferred_pharmacy": "pharmacy name if listed",
    "employment_status": "Employed/Self Employed/Unemployed/Retired/Student/Disabled/Military",
    "occupation": "job title if listed",
    "employer": "employer/company name",
    "employer_address": "employer address if listed",
    "insurance_provider": "insurance company name",
    "insurance_id": "policy number",
    "insurance_group_number": "group number",
    "insured_name": "name of insured person (may differ from patient)",
    "insured_dob": "YYYY-MM-DD",
    "insurance_phone": "insurance contact number",
    "billing_address": "billing address if different from home",
    "guarantor_name": "person responsible for payment if listed",
    "reason_for_visit": "chief complaint or reason for visit",
    "date_of_injury": "YYYY-MM-DD (date of injury or onset of symptoms)",
    "pain_location": "where pain is located if listed",
    "pain_onset": "when pain began (e.g. three months ago)",
    "pain_cause": "what caused the pain/condition",
    "pain_progression": "Improved/Worsened/Stayed the same",
    "work_related_injury": "Yes/No",
    "car_accident": "Yes/No",
    "medical_conditions": "existing medical conditions",
    "allergies": "known allergies",
    "medications": "current medications",
    "signature_date": "YYYY-MM-DD (date signed)"
}

IMPORTANT: Return ONLY the JSON object, no other text."""

    # Retry loop with progressively smaller images on memory errors
    max_retries = 3
    current_img = img
    current_bytes = image_bytes
    current_width, current_height = new_width, new_height
    current_size_kb = opt_size_kb

    for attempt in range(max_retries):
        est_img_tokens = (current_width // 14) * (current_height // 14)
        console.print(
            f"  Image: {current_width}x{current_height}, {current_size_kb:.0f}KB (~{est_img_tokens:,} tokens)"
        )

        if attempt == 0:
            console.print("  [dim]This may take 30-60 seconds...[/dim]")
        else:
            console.print(
                f"  [dim]Retry {attempt}/{max_retries-1} with smaller image...[/dim]"
            )

        try:
            start_time = time.time()
            raw_text = vlm.extract_from_image(
                image_bytes=current_bytes,
                prompt=extraction_prompt,
            )
            extraction_time = time.time() - start_time

            # Check for memory-related errors
            if (
                "failed to process image" in raw_text
                or "memory slot" in raw_text.lower()
            ):
                if attempt < max_retries - 1:
                    console.print(
                        "  [yellow]⚠[/yellow] Memory error, reducing image size..."
                    )
                    # Reduce image to 75% of current size
                    scale = 0.75
                    current_width = int(current_width * scale)
                    current_height = int(current_height * scale)
                    current_img = current_img.resize(
                        (current_width, current_height), Image.Resampling.LANCZOS
                    )
                    output = io.BytesIO()
                    current_img.save(
                        output, format="JPEG", quality=jpeg_quality, optimize=True
                    )
                    current_bytes = output.getvalue()
                    current_size_kb = len(current_bytes) / 1024
                    continue
                else:
                    console.print(f"  [red]✗[/red] {raw_text}")
                    console.print()
                    console.print("[yellow]Suggestions:[/yellow]")
                    console.print("  1. Try with smaller image: --max-dimension 640")
                    console.print("  2. Restart Lemonade Server to clear memory")
                    console.print("  3. Reload the VLM model in Lemonade")
                    return 1

            if raw_text.startswith("[VLM extraction failed:"):
                console.print(f"  [red]✗[/red] {raw_text}")
                return 1

            # Success!
            console.print(
                f"  [green]✓[/green] Extraction complete ({len(raw_text)} chars, {extraction_time:.1f}s)"
            )

            # Estimate tokens/sec (output tokens only)
            est_output_tokens = len(raw_text) / 4
            tps = est_output_tokens / extraction_time if extraction_time > 0 else 0
            console.print(
                f"  Output: ~{est_output_tokens:.0f} tokens at ~{tps:.1f} TPS"
            )

            # Total throughput including prompt processing
            total_tokens = (
                est_img_tokens + 200 + est_output_tokens
            )  # img + prompt + output
            total_tps = total_tokens / extraction_time if extraction_time > 0 else 0
            console.print(
                f"  Total throughput: ~{total_tps:.0f} TPS (incl. {est_img_tokens:,} image tokens)"
            )

            # Update dimensions for final report
            new_width, new_height = current_width, current_height
            break

        except Exception as e:
            console.print(f"  [red]✗[/red] Extraction failed: {e}")
            return 1
    else:
        # All retries exhausted
        console.print("  [red]✗[/red] All retries failed")
        return 1

    # Step 5: Parse JSON
    console.print()
    console.print("[bold]Step 5:[/bold] Parsing JSON...")
    try:
        # Try direct parse
        patient_data = json.loads(raw_text)
        console.print("  [green]✓[/green] JSON parsed successfully")
    except json.JSONDecodeError:
        # Try to find JSON in text
        try:
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            if start >= 0 and end > start:
                patient_data = json.loads(raw_text[start:end])
                console.print("  [green]✓[/green] JSON extracted from response")
            else:
                console.print("  [red]✗[/red] No JSON found in response")
                console.print()
                console.print("[bold]Raw VLM Output:[/bold]")
                console.print(raw_text[:500])
                return 1
        except json.JSONDecodeError as e:
            console.print(f"  [red]✗[/red] JSON parse failed: {e}")
            return 1

    # Display results
    console.print()
    console.print(
        Panel.fit(
            "[bold green]✓ Extraction Successful[/bold green]",
            border_style="green",
        )
    )

    # Show extracted fields
    console.print()
    console.print("[bold]Extracted Fields:[/bold]")
    fields_found = 0
    for key, value in patient_data.items():
        if value and value != "null":
            fields_found += 1
            console.print(f"  [cyan]{key}:[/cyan] {value}")

    console.print()
    console.print(f"[dim]Fields extracted: {fields_found}/53[/dim]")
    console.print()

    # Timing breakdown section
    console.print("[bold]⏱️  TIMING BREAKDOWN[/bold]")
    console.print("-" * 40)
    console.print(f"   Model:              {args.vlm_model}")
    console.print(f"   Image dimensions:   {new_width}x{new_height}")
    console.print(f"   VLM extraction:     {extraction_time:.2f}s")
    console.print(f"   Est. image tokens:  ~{est_img_tokens:,}")
    console.print(f"   Est. output tokens: ~{int(est_output_tokens)}")
    console.print(f"   Est. tokens/sec:    ~{tps:.1f} TPS")
    console.print()

    # Success summary
    if patient_data.get("first_name") and patient_data.get("last_name"):
        console.print(
            f"   Patient: {patient_data.get('first_name', '')} {patient_data.get('last_name', '')}"
        )
        console.print("   [green]✓ Pipeline test PASSED[/green]")
    else:
        console.print("   [yellow]⚠ Missing required name fields[/yellow]")
        console.print("   [red]✗ Pipeline test FAILED[/red]")
    console.print()

    return 0


def _launch_electron(url: str, delay: float = 1.5) -> bool:
    """
    Launch Electron app to display the dashboard.

    Returns True if Electron was launched successfully, False otherwise.
    """
    import os
    import platform
    import shutil
    import subprocess
    import time

    time.sleep(delay)  # Wait for server to start

    # Find the Electron wrapper directory
    electron_dir = Path(__file__).parent / "dashboard" / "electron"
    main_js = electron_dir / "main.js"

    if not main_js.exists():
        logger.warning(f"Electron wrapper not found at {electron_dir}")
        return False

    # On Windows, use npm.cmd and npx.cmd
    is_windows = platform.system() == "Windows"
    npm_cmd = "npm.cmd" if is_windows else "npm"
    npx_cmd = "npx.cmd" if is_windows else "npx"

    # Check if npx/electron is available
    npx_path = shutil.which(npx_cmd)
    if not npx_path:
        logger.warning(f"{npx_cmd} not found in PATH, cannot launch Electron")
        return False

    try:
        # Check if node_modules exists, if not run npm install first
        node_modules = electron_dir / "node_modules"
        if not node_modules.exists():
            console.print("[dim]Installing Electron dependencies...[/dim]")
            subprocess.run(
                [npm_cmd, "install"],
                cwd=str(electron_dir),
                capture_output=True,
                check=True,
                shell=is_windows,  # Use shell on Windows for .cmd files
            )

        # Launch Electron with the dashboard URL
        env = os.environ.copy()
        env["EMR_DASHBOARD_URL"] = url

        subprocess.Popen(
            [npx_cmd, "electron", "."],
            cwd=str(electron_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=is_windows,  # Use shell on Windows for .cmd files
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to launch Electron: {e}")
        return False


def cmd_dashboard(args):
    """Start web dashboard."""
    import threading
    import time
    import webbrowser

    def open_browser(url: str, delay: float = 1.5):
        """Open browser after a short delay to allow server to start."""
        time.sleep(delay)
        webbrowser.open(url)

    def open_electron_or_browser(url: str, use_browser: bool, delay: float = 1.5):
        """Open Electron app, falling back to browser if needed."""
        if use_browser:
            open_browser(url, delay)
        else:
            if not _launch_electron(url, delay):
                console.print(
                    "[yellow]Electron not available, opening in browser instead...[/yellow]"
                )
                open_browser(url, delay)

    try:
        from gaia.agents.emr.dashboard.server import run_dashboard

        console.print()
        console.print(
            Panel.fit(
                "[bold cyan]EMR Dashboard[/bold cyan]\n"
                "[dim]Real-time Patient Processing Monitor[/dim]",
                border_style="cyan",
            )
        )

        url = f"http://localhost:{args.port}"

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("📁 Watch folder:", args.watch_dir)
        table.add_row("💾 Database:", args.db)
        table.add_row("🌐 URL:", f"[bold cyan]{url}[/bold cyan]")
        console.print(table)
        console.print("\n[dim]Press Ctrl+C to stop[/dim]\n")

        # Auto-open unless --no-open flag is set
        if not getattr(args, "no_open", False):
            use_browser = getattr(args, "browser", False)
            if use_browser:
                console.print("[dim]Opening dashboard in browser...[/dim]\n")
            else:
                console.print("[dim]Opening dashboard in Electron app...[/dim]\n")

            open_thread = threading.Thread(
                target=open_electron_or_browser,
                args=(url, use_browser),
                daemon=True,
            )
            open_thread.start()

        run_dashboard(
            watch_dir=args.watch_dir,
            db_path=args.db,
            host=args.host,
            port=args.port,
        )
    except ImportError:
        console.print("[red]Error: Dashboard dependencies not installed[/red]")
        console.print("Install with: [cyan]pip install 'amd-gaia[api]'[/cyan]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down dashboard...[/dim]")
        return 0


def _add_common_args(parser):
    """Add common arguments to a parser."""
    parser.add_argument(
        "--watch-dir",
        default="./intake_forms",
        help="Directory to watch for intake forms (default: ./intake_forms)",
    )
    parser.add_argument(
        "--db",
        default="./data/patients.db",
        help="Path to patient database (default: ./data/patients.db)",
    )
    parser.add_argument(
        "--vlm-model",
        default="Qwen3-VL-4B-Instruct-GGUF",
        help="VLM model for extraction (default: Qwen3-VL-4B-Instruct-GGUF)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Medical Intake Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Watch command
    parser_watch = subparsers.add_parser(
        "watch",
        help="Watch directory for new intake forms",
    )
    _add_common_args(parser_watch)
    parser_watch.set_defaults(func=cmd_watch)

    # Process command
    parser_process = subparsers.add_parser(
        "process",
        help="Process a single intake form",
    )
    _add_common_args(parser_process)
    parser_process.add_argument("file", help="Path to intake form file")
    parser_process.set_defaults(func=cmd_process)

    # Query command
    parser_query = subparsers.add_parser(
        "query",
        help="Query patient database",
    )
    _add_common_args(parser_query)
    parser_query.add_argument("question", help="Question to ask")
    parser_query.set_defaults(func=cmd_query)

    # Stats command
    parser_stats = subparsers.add_parser(
        "stats",
        help="Show processing statistics",
    )
    _add_common_args(parser_stats)
    parser_stats.set_defaults(func=cmd_stats)

    # Reset command
    parser_reset = subparsers.add_parser(
        "reset",
        help="Clear all patient data from the database",
    )
    _add_common_args(parser_reset)
    parser_reset.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser_reset.set_defaults(func=cmd_reset)

    # Init command
    parser_init = subparsers.add_parser(
        "init",
        help="Download and setup required VLM models",
    )
    parser_init.add_argument(
        "--vlm-model",
        default="Qwen3-VL-4B-Instruct-GGUF",
        help="VLM model to download (default: Qwen3-VL-4B-Instruct-GGUF)",
    )
    parser_init.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser_init.set_defaults(func=cmd_init)

    # Test command
    parser_test = subparsers.add_parser(
        "test",
        help="Test VLM extraction pipeline on a single file",
    )
    parser_test.add_argument(
        "file",
        help="Path to intake form image (PNG, JPG, PDF)",
    )
    parser_test.add_argument(
        "--vlm-model",
        default="Qwen3-VL-4B-Instruct-GGUF",
        help="VLM model to use (default: Qwen3-VL-4B-Instruct-GGUF)",
    )
    parser_test.add_argument(
        "--max-dimension",
        type=int,
        default=1024,
        help="Max image dimension in pixels (default: 1024)",
    )
    parser_test.add_argument(
        "--jpeg-quality",
        type=int,
        default=85,
        help="JPEG compression quality (default: 85)",
    )
    parser_test.add_argument(
        "--clear-context",
        action="store_true",
        help="Clear VLM context before processing (fixes memory errors)",
    )
    parser_test.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser_test.set_defaults(func=cmd_test)

    # Dashboard command
    parser_dashboard = subparsers.add_parser(
        "dashboard",
        help="Start web dashboard",
    )
    _add_common_args(parser_dashboard)
    parser_dashboard.add_argument(
        "--host",
        default="127.0.0.1",
        help="Dashboard host (default: 127.0.0.1)",
    )
    parser_dashboard.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Dashboard port (default: 8080)",
    )
    parser_dashboard.add_argument(
        "--no-open",
        action="store_true",
        help="Don't automatically open dashboard",
    )
    parser_dashboard.add_argument(
        "--browser",
        action="store_true",
        help="Open in web browser instead of Electron app",
    )
    parser_dashboard.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()

    # Run command
    if not args.command:
        parser.print_help()
        return 0

    # Configure logging - WARNING by default, DEBUG with --debug flag
    if getattr(args, "debug", False):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        # Suppress all logs from gaia modules for cleaner output
        logging.basicConfig(level=logging.WARNING)
        for logger_name in [
            "gaia",
            "gaia.llm",
            "gaia.database",
            "gaia.agents",
            "gaia.utils",
        ]:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
