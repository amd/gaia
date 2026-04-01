#!/usr/bin/env python3
"""
GAIA Pipeline with Lemonade LLM Backend
========================================

Demonstrates running the GAIA recursive iterative pipeline against a live
Lemonade LLM server.  Performs a pre-flight health check before executing
the pipeline so users get a clear error message when the backend is not
running instead of a cryptic connection failure mid-pipeline.

Demo goals you can try:
    1. "Build a simple REST API with FastAPI"
    2. "Write a Python script to analyze CSV files"
    3. "Create a CLI tool for file management"

Usage:
    # Default goal
    python examples/pipeline_with_lemonade.py

    # Custom goal
    python examples/pipeline_with_lemonade.py "Write a Python script to analyze CSV files"

    # Specify template and model
    python examples/pipeline_with_lemonade.py "Create a CLI tool for file management" \
        --template rapid --model Qwen3-0.6B-GGUF

Exit codes:
    0 — success
    1 — pipeline error
    2 — Lemonade server not running
    3 — model not found
    4 — bad arguments
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------

# NOTE: The health probe uses the Ollama-compatible /api/tags endpoint
# (default port 11434).  Set LEMONADE_BASE_URL to point at your Lemonade
# instance if it is running on a different host or port.
LEMONADE_URL: str = os.environ.get("LEMONADE_BASE_URL", "http://localhost:11434")

# Default model to use when none is specified via --model or GAIA_MODEL_ID.
MODEL_ID: str = os.environ.get("GAIA_MODEL_ID", "Qwen3-0.6B-GGUF")

# Canonical agents directory relative to this file.
AGENTS_DIR: Path = Path(__file__).parent.parent / "config" / "agents"

# Demo goals bundled in the script for quick reference.
DEMO_GOALS = [
    "Build a simple REST API with FastAPI",
    "Write a Python script to analyze CSV files",
    "Create a CLI tool for file management",
]


# ---------------------------------------------------------------------------
# Pre-flight health check
# ---------------------------------------------------------------------------


def check_lemonade_running(base_url: str) -> bool:
    """
    Probe the Lemonade server to verify it is reachable.

    Sends a GET request to ``{base_url}/api/tags`` with a short timeout so
    the script fails fast if the backend is not running.

    Args:
        base_url: Root URL of the Lemonade server (e.g. ``http://localhost:11434``).

    Returns:
        ``True`` if the server responded with an HTTP success status,
        ``False`` otherwise.
    """
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=5)
        return response.status_code < 400
    except requests.exceptions.ConnectionError:
        return False
    except requests.exceptions.Timeout:
        return False
    except Exception:
        return False


def preflight_check(base_url: str) -> None:
    """
    Run the Lemonade pre-flight check and exit with code 2 on failure.

    Prints a clear status line in either case so users know immediately
    whether their backend is available.

    Args:
        base_url: Root URL of the Lemonade server.
    """
    if check_lemonade_running(base_url):
        print(f"Lemonade server is running at {base_url}")
    else:
        print(
            f"ERROR: Lemonade server not running at {base_url}.\n"
            "Start it with: lemonade-server serve",
            file=sys.stderr,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# Core pipeline coroutine
# ---------------------------------------------------------------------------


async def run_pipeline_with_lemonade(
    goal: str,
    template: str = "generic",
    model_id: str = None,
) -> dict:
    """
    Run the GAIA recursive iterative pipeline against a live Lemonade backend.

    Initialises a :class:`~gaia.pipeline.engine.PipelineEngine` with the
    requested template and model, executes the four-phase pipeline
    (PLANNING -> DEVELOPMENT -> QUALITY -> DECISION), then returns a plain
    dictionary summarising the outcome.

    Args:
        goal:     Natural-language description of what the pipeline should build.
        template: Template name — ``"generic"``, ``"rapid"``, or ``"enterprise"``.
        model_id: Lemonade model identifier.  Falls back to the module-level
                  :data:`MODEL_ID` constant when ``None``.

    Returns:
        Dictionary with the following keys:

        ``pipeline_id``
            Unique identifier for this pipeline run.
        ``state``
            Terminal state name (``"COMPLETED"``, ``"FAILED"``, etc.).
        ``quality_score``
            Float quality score produced by the QUALITY phase, or ``None``.
        ``iteration_count``
            Number of planning/development/quality loops executed.
        ``artifacts``
            Dict of named artifacts produced during execution.
        ``defects``
            List of defects detected by the quality gate.
        ``error_message``
            Error description if the pipeline failed, otherwise ``None``.
        ``elapsed_seconds``
            Wall-clock seconds from READY to terminal state, or ``None``.

    Raises:
        SystemExit(1): On unexpected pipeline initialisation or execution error.
    """
    # Resolve imports here so errors surface with a meaningful traceback.
    from gaia.pipeline.engine import PipelineEngine
    from gaia.pipeline.state import PipelineContext

    resolved_model = model_id or MODEL_ID
    agents_dir = str(AGENTS_DIR) if AGENTS_DIR.exists() else None

    print(f"Template  : {template}")
    print(f"Model     : {resolved_model}")
    print(f"Goal      : {goal}")
    print(f"Agents dir: {agents_dir or '(auto-detected)'}")
    print()

    # Build an immutable PipelineContext.
    pipeline_id = f"lemonade-demo-{asyncio.get_running_loop().time():.0f}"
    context = PipelineContext(
        pipeline_id=pipeline_id,
        user_goal=goal,
        quality_threshold=0.90,
        max_iterations=10,
        concurrent_loops=5,
    )

    # Construct the engine wired to the resolved model.
    engine = PipelineEngine(
        agents_dir=agents_dir,
        enable_logging=True,
        log_level=logging.WARNING,
        max_concurrent_loops=100,
        worker_pool_size=4,
        model_id=resolved_model,
    )

    try:
        print(f"Initialising pipeline '{pipeline_id}'...")
        await engine.initialize(
            context,
            config={
                "template": template,
                "enable_hooks": True,
            },
        )

        print("Starting pipeline execution...")
        snapshot = await engine.start()

    except Exception as exc:
        print(f"ERROR: Pipeline execution failed: {exc}", file=sys.stderr)
        engine.shutdown()
        sys.exit(1)

    engine.shutdown()

    elapsed = snapshot.elapsed_time()

    return {
        "pipeline_id": pipeline_id,
        "state": snapshot.state.name,
        "quality_score": snapshot.quality_score,
        "iteration_count": snapshot.iteration_count,
        "artifacts": snapshot.artifacts,
        "defects": snapshot.defects,
        "error_message": snapshot.error_message,
        "elapsed_seconds": elapsed,
    }


# ---------------------------------------------------------------------------
# Result printer
# ---------------------------------------------------------------------------


def print_results(result: dict) -> None:
    """Print a structured summary of the pipeline result."""
    width = 60
    print("\n" + "=" * width)
    print("PIPELINE RESULT")
    print("=" * width)
    print(f"Pipeline ID    : {result['pipeline_id']}")
    print(f"Final state    : {result['state']}")
    print(f"Iterations run : {result['iteration_count']}")

    if result["quality_score"] is not None:
        score = result["quality_score"]
        verdict = "PASS" if score >= 0.90 else "FAIL"
        print(f"Quality score  : {score:.3f}  [{verdict}]")
    else:
        print("Quality score  : not evaluated")

    elapsed = result["elapsed_seconds"]
    if elapsed is not None:
        print(f"Elapsed time   : {elapsed:.2f}s")
    else:
        print("Elapsed time   : N/A")

    artifacts = result.get("artifacts") or {}
    if artifacts:
        print(f"Artifacts ({len(artifacts)}):")
        for key, value in artifacts.items():
            if isinstance(value, dict):
                repr_val = f"<dict with {len(value)} keys>"
            else:
                repr_val = repr(value)[:80]
            print(f"    {key}: {repr_val}")
    else:
        print("Artifacts      : none")

    defects = result.get("defects") or []
    print(f"Defects found  : {len(defects)}")

    if result.get("error_message"):
        print(f"\nERROR: {result['error_message']}")

    print("=" * width)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the GAIA recursive iterative pipeline with a live Lemonade backend.\n\n"
            "Demo goals:\n"
            + "\n".join(f"  {i + 1}. {g}" for i, g in enumerate(DEMO_GOALS))
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "goal",
        nargs="?",
        default=DEMO_GOALS[0],
        help=(
            "Natural-language pipeline goal "
            f'(default: "{DEMO_GOALS[0]}")'
        ),
    )
    parser.add_argument(
        "--template",
        default="generic",
        choices=["generic", "rapid", "enterprise"],
        help="Pipeline template to use (default: generic)",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help=f"Lemonade model ID (default: {MODEL_ID})",
    )
    parser.add_argument(
        "--lemonade-url",
        default=LEMONADE_URL,
        metavar="URL",
        help=f"Lemonade server base URL (default: {LEMONADE_URL})",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    parser = build_parser()

    try:
        args = parser.parse_args()
    except SystemExit:
        sys.exit(4)

    # Pre-flight check — exits with code 2 if Lemonade is not running.
    preflight_check(args.lemonade_url)

    # Run the pipeline.
    result = asyncio.run(
        run_pipeline_with_lemonade(
            goal=args.goal,
            template=args.template,
            model_id=args.model,
        )
    )

    # Display results.
    print_results(result)

    # Exit 0 on success, 1 on pipeline failure.
    if result["state"] not in ("COMPLETED",):
        sys.exit(1)

    sys.exit(0)
