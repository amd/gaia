"""
GAIA Pipeline — Batch Execution with Backpressure
===================================================

Demonstrates running multiple independent pipeline contexts concurrently,
with bounded concurrency to prevent resource exhaustion.

Key concepts covered:
- Creating multiple PipelineContext objects with different user goals
- Factory pattern: create-initialize-start per workload
- Bounded concurrency using asyncio.Semaphore (mirroring the PipelineEngine's
  dual-semaphore design: max_concurrent_loops + worker_pool_size)
- Progress callback tracking
- Collecting and comparing results across pipeline runs
- execute_with_backpressure() API: per-engine versus per-workload semantics

Design note on execute_with_backpressure():
  The PipelineEngine.execute_with_backpressure() method is designed to pass
  multiple workloads through a SINGLE engine's execute() method.  Because
  execute() delegates to start() (which is a one-shot operation — the state
  machine reaches a terminal state after the first call), true multi-context
  batch execution requires one engine instance per context.

  This example demonstrates the production-correct pattern:
  - A bounded async factory function creates and runs one engine per workload.
  - A shared asyncio.Semaphore limits how many engines execute concurrently,
    mirroring the engine's own worker_pool_size / max_concurrent_loops params.
  - execute_with_backpressure() is shown in its literal form so readers can
    understand the API contract it provides at the single-engine level.

Run this script from the repository root:
    python examples/pipeline_batch.py
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext, PipelineSnapshot

# Graceful fallback if metrics extras are not installed.
try:
    from gaia.pipeline.metrics import MetricsCollector  # noqa: F401

    HAS_METRICS = True
except ImportError:
    HAS_METRICS = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENTS_DIR = Path(__file__).parent.parent / "config" / "agents"

# Concurrency limits — mirrors the PipelineEngine constructor params.
MAX_CONCURRENT_LOOPS = 10   # max_concurrent_loops passed to each engine
WORKER_POOL_SIZE = 2        # worker_pool_size passed to each engine
BATCH_CONCURRENCY = 3       # how many batch workloads may run simultaneously

# The five different goals for the batch run.
BATCH_GOALS = [
    "Build a real-time chat application with WebSocket support",
    "Design a GraphQL API for an e-commerce product catalog",
    "Implement a CI/CD pipeline with GitHub Actions and Docker",
    "Create a data ingestion service for streaming IoT sensor data",
    "Develop a recommendation engine using collaborative filtering",
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    """Collects per-pipeline outcome for comparison."""

    pipeline_id: str
    user_goal: str
    state: str
    quality_score: Optional[float]
    iteration_count: int
    artifact_count: int
    defect_count: int
    elapsed_seconds: Optional[float]
    error_message: Optional[str]


# ---------------------------------------------------------------------------
# Per-pipeline factory (the core execution unit)
# ---------------------------------------------------------------------------


async def run_single_pipeline(
    context: PipelineContext,
    template: str,
    progress_callback: Optional[Callable[[BatchResult], None]] = None,
) -> BatchResult:
    """
    Create, initialize, run, and shut down one PipelineEngine for a context.

    This is the production-correct pattern for independent batch workloads:
    each context gets its own engine instance with its own semaphores,
    state machine, agent registry, and hook system.

    Args:
        context: Immutable pipeline context describing the workload.
        template: Template name ('generic', 'rapid', or 'enterprise').
        progress_callback: Optional callable invoked with the BatchResult
                           immediately after this pipeline completes.

    Returns:
        BatchResult with outcome fields for comparison.
    """
    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=False,        # Suppress per-engine logs in batch mode.
        log_level=logging.CRITICAL,  # Only critical errors make it through.
        max_concurrent_loops=MAX_CONCURRENT_LOOPS,
        worker_pool_size=WORKER_POOL_SIZE,
    )

    snapshot: Optional[PipelineSnapshot] = None
    error_msg: Optional[str] = None

    try:
        await engine.initialize(context, config={"template": template})
        snapshot = await engine.start()
    except Exception as exc:
        error_msg = str(exc)
    finally:
        engine.shutdown()

    # Build the result record.
    if snapshot is not None:
        result = BatchResult(
            pipeline_id=context.pipeline_id,
            user_goal=context.user_goal,
            state=snapshot.state.name,
            quality_score=snapshot.quality_score,
            iteration_count=snapshot.iteration_count,
            artifact_count=len(snapshot.artifacts),
            defect_count=len(snapshot.defects),
            elapsed_seconds=snapshot.elapsed_time(),
            error_message=snapshot.error_message or error_msg,
        )
    else:
        result = BatchResult(
            pipeline_id=context.pipeline_id,
            user_goal=context.user_goal,
            state="FAILED",
            quality_score=None,
            iteration_count=0,
            artifact_count=0,
            defect_count=0,
            elapsed_seconds=None,
            error_message=error_msg,
        )

    if progress_callback:
        progress_callback(result)

    return result


# ---------------------------------------------------------------------------
# Bounded batch executor
# ---------------------------------------------------------------------------


async def run_batch(
    contexts: List[PipelineContext],
    template: str = "generic",
    max_concurrent: int = BATCH_CONCURRENCY,
    progress_callback: Optional[Callable[[BatchResult], None]] = None,
) -> List[BatchResult]:
    """
    Run multiple pipelines concurrently with bounded concurrency.

    Uses an asyncio.Semaphore to cap how many pipelines are active at the
    same time, mirroring the dual-semaphore design inside PipelineEngine
    (max_concurrent_loops + worker_pool_size).

    Args:
        contexts: List of PipelineContext objects to process.
        template: Template name for all pipelines in this batch.
        max_concurrent: Maximum number of concurrently running pipelines.
        progress_callback: Invoked after each pipeline completes.

    Returns:
        List of BatchResult in the same order as contexts.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_run(ctx: PipelineContext) -> BatchResult:
        async with semaphore:
            return await run_single_pipeline(ctx, template, progress_callback)

    # asyncio.gather preserves input order and returns exceptions as values
    # (not raised) when return_exceptions=True — same contract as the engine's
    # execute_with_backpressure().
    tasks = [bounded_run(ctx) for ctx in contexts]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Unwrap: if a task raised an exception, substitute a FAILED BatchResult.
    results: List[BatchResult] = []
    for ctx, raw in zip(contexts, raw_results):
        if isinstance(raw, Exception):
            results.append(
                BatchResult(
                    pipeline_id=ctx.pipeline_id,
                    user_goal=ctx.user_goal,
                    state="ERROR",
                    quality_score=None,
                    iteration_count=0,
                    artifact_count=0,
                    defect_count=0,
                    elapsed_seconds=None,
                    error_message=str(raw),
                )
            )
        else:
            results.append(raw)

    return results


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def print_batch_summary(results: List[BatchResult]) -> None:
    """Print a comparison table across all batch results."""
    print()
    print("=" * 90)
    print("BATCH COMPARISON SUMMARY")
    print("=" * 90)

    header = (
        f"{'ID':<16} {'State':<12} {'Quality':>8} {'Iters':>6} "
        f"{'Artifacts':>10} {'Defects':>8} {'Elapsed':>9}"
    )
    print(header)
    print("-" * 90)

    for r in results:
        quality_str = f"{r.quality_score:.3f}" if r.quality_score is not None else "  N/A "
        elapsed_str = f"{r.elapsed_seconds:.2f}s" if r.elapsed_seconds is not None else "  N/A"
        print(
            f"{r.pipeline_id:<16} {r.state:<12} {quality_str:>8} {r.iteration_count:>6} "
            f"{r.artifact_count:>10} {r.defect_count:>8} {elapsed_str:>9}"
        )
        if r.error_message:
            print(f"  -> Error: {r.error_message[:80]}")

    print("-" * 90)

    # Aggregate statistics.
    completed = [r for r in results if r.state == "COMPLETED"]
    failed = [r for r in results if r.state not in ("COMPLETED",)]
    quality_scores = [r.quality_score for r in completed if r.quality_score is not None]

    print(f"\nTotal pipelines   : {len(results)}")
    print(f"Completed         : {len(completed)}")
    print(f"Failed/Error      : {len(failed)}")

    if quality_scores:
        avg_q = sum(quality_scores) / len(quality_scores)
        max_q = max(quality_scores)
        min_q = min(quality_scores)
        print(f"Avg quality score : {avg_q:.3f}")
        print(f"Best quality score: {max_q:.3f}")
        print(f"Worst quality score: {min_q:.3f}")

    elapsed_vals = [r.elapsed_seconds for r in results if r.elapsed_seconds is not None]
    if elapsed_vals:
        total_elapsed = sum(elapsed_vals)
        print(f"Total wall time   : {total_elapsed:.2f}s (sequential equivalent)")


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def run_batch_demo() -> None:
    """Demonstrate bounded batch execution across 5 different pipeline goals."""

    # ------------------------------------------------------------------
    # Step 1: Build the 5 PipelineContext objects.
    #
    # Each gets a unique pipeline_id and a different user_goal.  The
    # quality_threshold is intentionally varied across workloads to show
    # how different contexts produce different outcomes.
    # ------------------------------------------------------------------
    thresholds = [0.85, 0.90, 0.90, 0.85, 0.95]

    contexts = [
        PipelineContext(
            pipeline_id=f"batch-{i + 1:03d}",
            user_goal=goal,
            quality_threshold=thresholds[i],
            max_iterations=5,   # Keep short for the demo.
        )
        for i, goal in enumerate(BATCH_GOALS)
    ]

    print("=" * 70)
    print("BATCH PIPELINE EXECUTION")
    print("=" * 70)
    print(f"Workloads         : {len(contexts)}")
    print(f"Template          : generic")
    print(f"Max concurrent    : {BATCH_CONCURRENCY}")
    print(f"worker_pool_size  : {WORKER_POOL_SIZE}")
    print(f"max_concurrent_loops: {MAX_CONCURRENT_LOOPS}")
    print()

    for ctx in contexts:
        print(
            f"  {ctx.pipeline_id}  threshold={ctx.quality_threshold:.0%}  "
            f'goal="{ctx.user_goal[:55]}..."'
        )
    print()

    # ------------------------------------------------------------------
    # Step 2: Set up the progress callback.
    #
    # The callback is invoked immediately after each pipeline completes,
    # allowing real-time progress reporting rather than waiting for all
    # pipelines to finish.
    # ------------------------------------------------------------------
    completed_count = 0
    batch_start = time.monotonic()

    def on_pipeline_complete(result: BatchResult) -> None:
        nonlocal completed_count
        completed_count += 1
        elapsed = time.monotonic() - batch_start
        quality_str = (
            f"score={result.quality_score:.3f}"
            if result.quality_score is not None
            else "score=N/A"
        )
        print(
            f"  [{completed_count}/{len(contexts)}] {result.pipeline_id}  "
            f"{result.state}  {quality_str}  ({elapsed:.1f}s elapsed)"
        )

    # ------------------------------------------------------------------
    # Step 3: Execute the batch with bounded concurrency.
    # ------------------------------------------------------------------
    print("Running batch...")
    results = await run_batch(
        contexts=contexts,
        template="generic",
        max_concurrent=BATCH_CONCURRENCY,
        progress_callback=on_pipeline_complete,
    )

    total_wall_time = time.monotonic() - batch_start
    print(f"\nBatch complete in {total_wall_time:.2f}s total wall time.")

    # ------------------------------------------------------------------
    # Step 4: Demonstrate execute_with_backpressure() at the single-engine
    # level for completeness.
    #
    # For an UN-initialized engine, execute(workload) returns the workload
    # unchanged.  This is useful for queueing/passthrough scenarios where
    # the engine acts as a flow-control primitive before initialization.
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("execute_with_backpressure() SINGLE-ENGINE DEMO")
    print("=" * 70)

    passthrough_engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=False,
        log_level=logging.CRITICAL,
        max_concurrent_loops=MAX_CONCURRENT_LOOPS,
        worker_pool_size=WORKER_POOL_SIZE,
    )

    # Pass plain dict workloads through an un-initialized engine.
    # execute() returns them unchanged because _initialized is False.
    sample_workloads = [
        {"id": "wl-001", "priority": "high"},
        {"id": "wl-002", "priority": "normal"},
        {"id": "wl-003", "priority": "low"},
    ]

    passthrough_results = await passthrough_engine.execute_with_backpressure(
        workloads=sample_workloads,
        progress_callback=lambda r: print(f"  Passthrough result: {r}"),
    )

    print(f"Workloads in : {len(sample_workloads)}")
    print(f"Results out  : {len(passthrough_results)}")
    print(
        "Note: Un-initialized engine returns workloads unchanged — "
        "use the bounded factory pattern above for true batch pipeline runs."
    )
    passthrough_engine.shutdown()

    # ------------------------------------------------------------------
    # Step 5: Print the comparison table.
    # ------------------------------------------------------------------
    print_batch_summary(results)

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_batch_demo())
