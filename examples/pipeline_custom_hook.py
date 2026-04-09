"""
GAIA Pipeline — Custom Hook Injection
======================================

Demonstrates how to subclass BaseHook and register a custom hook that
records wall-clock timing for every pipeline phase.

Hook system fundamentals (from hooks/base.py and hooks/registry.py):
- Each hook subclass declares class-level metadata:
    name     : unique string identifier
    event    : event name this hook handles, or '*' to handle all events
    priority : HookPriority.HIGH / NORMAL / LOW (controls execution order)
    blocking : if True, a hook failure halts the pipeline
- Hooks are registered on a HookRegistry instance.
- The PipelineEngine's hook registry is stored in engine._hook_registry.
  There is no public accessor method, so we use the private attribute
  directly — this is an intentional escape hatch for extension.

PhaseTimingHook strategy:
- event = '*' so it fires for every event
- Check context.event inside execute() for 'PHASE_ENTER' and 'PHASE_EXIT'
- Store start/end timestamps in an internal dict keyed by phase name
- After the pipeline run, call hook.get_timings() to read the data

Run this script from the repository root:
    python examples/pipeline_custom_hook.py
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext
from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority

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


# ---------------------------------------------------------------------------
# Custom hook implementation
# ---------------------------------------------------------------------------


class PhaseTimingHook(BaseHook):
    """
    Custom hook that records wall-clock start and end times for each phase.

    Design decisions:
    - event = '*' so the hook receives PHASE_ENTER and PHASE_EXIT events
      (and all other events) without needing two separate subclasses.
    - priority = HookPriority.LOW so timing runs after all validation and
      context-injection hooks have fired, giving a more accurate measure of
      actual phase processing time.
    - blocking = False so a timing failure never halts the pipeline.

    Usage::

        hook = PhaseTimingHook()
        engine._hook_registry.register(hook)
        await engine.start()
        for phase, (start, end) in hook.get_timings().items():
            print(f"{phase}: {end - start:.3f}s")
    """

    # Class-level hook metadata — required by BaseHook.
    name = "phase_timing"
    event = "*"                    # Receive all events; filter in execute().
    priority = HookPriority.LOW   # Run after critical/normal hooks.
    blocking = False               # Never block the pipeline.
    description = "Records wall-clock timing for each pipeline phase."

    def __init__(self) -> None:
        super().__init__()
        # _start_times: phase_name -> float (time.monotonic() at PHASE_ENTER)
        self._start_times: Dict[str, float] = {}
        # _timings: phase_name -> (start, end) in seconds since epoch (monotonic)
        self._timings: Dict[str, Tuple[float, float]] = {}

    async def execute(self, context: HookContext) -> HookResult:
        """
        Record timing data when a PHASE_ENTER or PHASE_EXIT event fires.

        All other events pass through immediately with a success result.

        Args:
            context: HookContext supplied by the HookExecutor.
                     context.event is the event name string.
                     context.phase is the current pipeline phase name.

        Returns:
            HookResult.success_result() in all cases — this hook is
            purely observational and never modifies pipeline state.
        """
        event = context.event
        phase = context.phase

        if event == "PHASE_ENTER" and phase:
            # Record the monotonic start time for this phase.
            self._start_times[phase] = time.monotonic()

        elif event == "PHASE_EXIT" and phase:
            # Record the end time and compute the elapsed duration.
            end_time = time.monotonic()
            start_time = self._start_times.get(phase)
            if start_time is not None:
                self._timings[phase] = (start_time, end_time)
            else:
                # PHASE_EXIT without a corresponding PHASE_ENTER can happen
                # if the hook was registered after PHASE_ENTER fired.  Store
                # a zero-duration entry so the phase still appears in output.
                self._timings[phase] = (end_time, end_time)

        # Return a plain success result — no modifications to pipeline state.
        return HookResult.success_result(
            metadata={"event": event, "phase": phase or "N/A"}
        )

    def get_timings(self) -> Dict[str, Tuple[float, float]]:
        """
        Return a snapshot of recorded (start, end) monotonic times by phase.

        Returns:
            dict mapping phase_name -> (start_monotonic, end_monotonic)
        """
        return dict(self._timings)

    def get_elapsed_seconds(self) -> Dict[str, float]:
        """
        Return elapsed time in seconds for each completed phase.

        Returns:
            dict mapping phase_name -> elapsed_seconds
        """
        return {
            phase: end - start
            for phase, (start, end) in self._timings.items()
        }

    def has_incomplete_phases(self) -> bool:
        """True if any phase was entered but not exited (pipeline interrupted)."""
        return bool(set(self._start_times) - set(self._timings))


# ---------------------------------------------------------------------------
# Printing helpers
# ---------------------------------------------------------------------------


def print_timing_report(hook: PhaseTimingHook) -> None:
    """Print a formatted timing report from a PhaseTimingHook."""
    elapsed = hook.get_elapsed_seconds()

    if not elapsed:
        print("  No phase timings were recorded.")
        return

    total = sum(elapsed.values())
    max_elapsed = max(elapsed.values()) if elapsed else 1.0

    print(f"  {'Phase':<14} {'Elapsed':>10}   {'% of total':>10}   Bar")
    print(f"  {'-' * 14} {'-' * 10}   {'-' * 10}   {'-' * 20}")

    for phase, secs in elapsed.items():
        pct = secs / total * 100 if total > 0 else 0
        bar_len = int(secs / max_elapsed * 20)
        bar = "#" * bar_len
        print(f"  {phase:<14} {secs:>10.3f}s  {pct:>10.1f}%   [{bar:<20}]")

    print(f"  {'TOTAL':<14} {total:>10.3f}s")

    if hook.has_incomplete_phases():
        incomplete = set(hook._start_times) - set(hook._timings)
        print(f"\n  Warning: phases entered but not exited: {incomplete}")

    print(f"\n  Hook execution count: {hook.execution_count}")


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def run_with_custom_hook() -> None:
    """Demonstrate registering a custom hook and reading timing data after run."""

    # ------------------------------------------------------------------
    # Step 1: Create the timing hook instance before engine initialization.
    #
    # We instantiate the hook early so we can register it right after
    # engine.initialize() sets up engine._hook_registry.
    # ------------------------------------------------------------------
    timing_hook = PhaseTimingHook()
    print(f"Created hook: '{timing_hook.name}'")
    print(f"  Handles events: {timing_hook.event!r} (wildcard = all events)")
    print(f"  Priority: {timing_hook.priority.name}")
    print(f"  Blocking: {timing_hook.blocking}")
    print()

    # ------------------------------------------------------------------
    # Step 2: Build the pipeline context and engine.
    # ------------------------------------------------------------------
    context = PipelineContext(
        pipeline_id="hook-demo-001",
        user_goal="Build a microservices architecture with service mesh and observability",
        quality_threshold=0.90,
        max_iterations=5,
    )

    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=True,
        log_level=logging.WARNING,
        max_concurrent_loops=100,
        worker_pool_size=4,
    )

    # ------------------------------------------------------------------
    # Step 3: Initialize the engine (this wires up engine._hook_registry).
    # ------------------------------------------------------------------
    await engine.initialize(
        context,
        config={"template": "generic", "enable_hooks": True},
    )

    # ------------------------------------------------------------------
    # Step 4: Register the custom hook on the engine's hook registry.
    #
    # engine._hook_registry is a HookRegistry instance.  There is no
    # public get_hook_registry() accessor — the underscore prefix is the
    # engine's signal that this is an extension point, not a guaranteed
    # stable public API.  The registry's register() method is safe to call
    # at any point before or during execution.
    # ------------------------------------------------------------------
    if engine._hook_registry is None:
        print("ERROR: Hook registry is None — was enable_hooks=True?")
        engine.shutdown()
        return

    engine._hook_registry.register(timing_hook)

    # Confirm registration by inspecting the registry statistics.
    reg_stats = engine._hook_registry.get_statistics()
    print(f"Hook registry after registration:")
    print(f"  Total hooks    : {reg_stats['total_hooks']}")
    print(f"  Global hooks   : {reg_stats['global_hooks']}  (event='*')")
    print(f"  Unique hook names: {reg_stats['unique_hook_names']}")
    print()

    # ------------------------------------------------------------------
    # Step 5: Run the pipeline.
    # ------------------------------------------------------------------
    print(f"Running pipeline '{context.pipeline_id}'...")
    snapshot = await engine.start()

    print(f"Pipeline finished: {snapshot.state.name}")
    print()

    # ------------------------------------------------------------------
    # Step 6: Read and display the timing data collected by our hook.
    # ------------------------------------------------------------------
    print("=" * 60)
    print("PHASE TIMING REPORT  (collected by PhaseTimingHook)")
    print("=" * 60)
    print_timing_report(timing_hook)

    # ------------------------------------------------------------------
    # Step 7: Show that the hook's execution count tracks how many times
    # it fired (once per event, across all events).
    # ------------------------------------------------------------------
    print()
    print(f"Total hook executions: {timing_hook.execution_count}")
    print(f"Chronicle events total: {len(engine.get_chronicle())}")

    # Cross-check: snapshot.elapsed_time() vs. sum of per-phase timings.
    elapsed_total = snapshot.elapsed_time()
    phase_total = sum(timing_hook.get_elapsed_seconds().values())
    if elapsed_total is not None:
        print(f"\nSnapshot elapsed_time(): {elapsed_total:.3f}s")
        print(f"Sum of phase timings  : {phase_total:.3f}s")
        print(
            "(Difference is time spent in state-machine overhead "
            "and hook dispatch outside phase boundaries.)"
        )

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")

    engine.shutdown()
    print("\nEngine shut down cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_with_custom_hook())
