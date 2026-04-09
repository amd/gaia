"""
GAIA Pipeline Quickstart
========================

Demonstrates the minimal working pipeline run using the 'generic' template.

This example shows:
- How to build a PipelineContext with the correct agents_dir path
- How to choose a template by name ('generic', 'rapid', or 'enterprise')
- How to initialize and start the PipelineEngine
- How to print PipelineSnapshot fields in a meaningful way
- Graceful handling of optional gaia.pipeline.metrics import

Run this script from the repository root:
    python examples/pipeline_quickstart.py
"""

import asyncio
import logging
from pathlib import Path

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext, PipelineState

# gaia.pipeline.metrics is optional in some builds; guard the import so the
# quickstart still works on installations that omit the metrics extras.
try:
    from gaia.pipeline.metrics import MetricsCollector  # noqa: F401

    HAS_METRICS = True
except ImportError:
    HAS_METRICS = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve the canonical agents directory relative to this file so the
# quickstart works regardless of the working directory from which it is run.
AGENTS_DIR = Path(__file__).parent.parent / "config" / "agents"

# Select one of the three built-in templates.
#   "generic"    — quality threshold 0.90, up to 10 iterations (good default)
#   "rapid"      — quality threshold 0.75, up to  5 iterations (prototypes)
#   "enterprise" — quality threshold 0.95, up to 15 iterations (production)
TEMPLATE_NAME = "generic"


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def run_quickstart() -> None:
    """Run the minimal pipeline and display the resulting snapshot."""

    # ------------------------------------------------------------------
    # Step 1: Build an immutable PipelineContext.
    #
    # PipelineContext is a frozen dataclass; all configuration for this
    # specific pipeline run lives here and cannot change during execution.
    # ------------------------------------------------------------------
    context = PipelineContext(
        pipeline_id="quickstart-001",
        user_goal="Build a simple REST API with user authentication and JWT tokens",
        # quality_threshold defaults to 0.90 — keep it explicit for clarity.
        quality_threshold=0.90,
        # max_iterations caps the planning/development/quality loop count.
        max_iterations=10,
        # concurrent_loops controls how many loops the LoopManager may run at once.
        concurrent_loops=5,
    )

    # ------------------------------------------------------------------
    # Step 2: Construct the PipelineEngine.
    #
    # Pass agents_dir so the engine can discover YAML agent definitions.
    # log_level=30 (WARNING) keeps the quickstart output readable; set to
    # 20 (INFO) or 10 (DEBUG) for more verbose output.
    # ------------------------------------------------------------------
    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=True,
        log_level=logging.WARNING,
        max_concurrent_loops=100,
        worker_pool_size=4,
    )

    # ------------------------------------------------------------------
    # Step 3: Initialize the engine with context + config dict.
    #
    # The config dict is merged with the context at runtime.  Supplying
    # "template" here wires the engine to use the named RecursivePipelineTemplate
    # which controls agent selection and phase exit criteria.
    # ------------------------------------------------------------------
    print(f"Initializing pipeline '{context.pipeline_id}'...")
    print(f"  Goal      : {context.user_goal}")
    print(f"  Template  : {TEMPLATE_NAME}")
    print(f"  Threshold : {context.quality_threshold:.0%}")
    print(f"  Max iters : {context.max_iterations}")
    print(f"  Agents dir: {AGENTS_DIR}")
    print()

    await engine.initialize(
        context,
        config={
            "template": TEMPLATE_NAME,
            # enable_hooks defaults to True in the engine; including it here
            # makes the configuration intent explicit.
            "enable_hooks": True,
        },
    )

    # ------------------------------------------------------------------
    # Step 4: Start the pipeline.
    #
    # start() drives the engine through PLANNING -> DEVELOPMENT -> QUALITY
    # -> DECISION phases and returns a PipelineSnapshot once all phases are
    # complete (or the pipeline reaches a terminal state).
    # ------------------------------------------------------------------
    print("Starting pipeline execution...")
    snapshot = await engine.start()

    # ------------------------------------------------------------------
    # Step 5: Inspect the snapshot.
    #
    # PipelineSnapshot is a mutable dataclass that the state machine
    # populates during execution.  All fields below are always present;
    # optional ones (quality_score, error_message, elapsed_time) may be
    # None if the pipeline did not reach the relevant phase.
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PIPELINE RESULT SUMMARY")
    print("=" * 60)

    # snapshot.state is a PipelineState enum member.  Use .name for a
    # human-readable string (e.g. "COMPLETED", "FAILED", "CANCELLED").
    print(f"Final state    : {snapshot.state.name}")

    # is_terminal() is True for COMPLETED / FAILED / CANCELLED.
    terminal_label = "(terminal)" if snapshot.state.is_terminal() else "(active)"
    print(f"               : {terminal_label}")

    print(f"Last phase     : {snapshot.current_phase or 'N/A'}")
    print(f"Iterations run : {snapshot.iteration_count}")

    # quality_score is set by the QUALITY phase scorer; it may be None if
    # the pipeline failed before reaching quality evaluation.
    if snapshot.quality_score is not None:
        passed = snapshot.quality_score >= context.quality_threshold
        verdict = "PASS" if passed else "FAIL"
        print(f"Quality score  : {snapshot.quality_score:.3f}  [{verdict}]")
    else:
        print("Quality score  : not evaluated")

    # elapsed_time() computes wall-clock seconds between READY->RUNNING
    # and the terminal state timestamp.
    elapsed = snapshot.elapsed_time()
    print(f"Elapsed time   : {elapsed:.2f}s" if elapsed is not None else "Elapsed time   : N/A")

    # artifacts is a dict populated by each phase; keys reveal what was
    # produced (e.g. 'planning_agent', 'quality_report', 'decision').
    if snapshot.artifacts:
        print(f"Artifacts ({len(snapshot.artifacts)}):")
        for key, value in snapshot.artifacts.items():
            # Print the key and a type hint; avoid printing large dicts inline.
            value_repr = (
                f"<dict with {len(value)} keys>"
                if isinstance(value, dict)
                else repr(value)[:80]
            )
            print(f"    {key}: {value_repr}")
    else:
        print("Artifacts      : none")

    # defects is populated by the QualityGate and DefectExtraction hooks.
    print(f"Defects found  : {len(snapshot.defects)}")

    # chronicle is the ordered event log.  Each entry is a dict with at
    # minimum 'event', 'timestamp', 'from_state'/'to_state' (for state
    # transitions) or 'phase' and 'data' (for phase events).
    print(f"Chronicle events: {len(snapshot.chronicle)}")

    if snapshot.error_message:
        print(f"\nERROR: {snapshot.error_message}")

    print("=" * 60)

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")

    # ------------------------------------------------------------------
    # Step 6: Shutdown.
    #
    # shutdown() stops the LoopManager thread pool, the AgentRegistry file
    # watcher, and the QualityScorer.  Always call this to avoid resource
    # leaks.
    # ------------------------------------------------------------------
    engine.shutdown()
    print("\nEngine shut down cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_quickstart())
