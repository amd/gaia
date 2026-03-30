"""
GAIA Pipeline — Enterprise Template
=====================================

Demonstrates using the 'enterprise' template, which is the highest-fidelity
configuration available in the recursive pipeline system.

Enterprise template characteristics (from recursive_template.py):
  - quality_threshold : 0.95  (95% — stricter than generic's 90%)
  - max_iterations    : 15    (more remediation passes allowed)
  - planning agents   : planning-analysis-strategist, solutions-architect
  - quality agents    : quality-reviewer, security-auditor, performance-analyst
  - routing rules     : security defects -> security-auditor
                        performance defects -> performance-analyst

This example shows:
- Inspecting the template definition before running the pipeline
- Comparing agent roster per phase vs the generic template
- Running a full enterprise pipeline
- Interpreting artifacts keyed by phase (planning_agent, quality_report, decision)
- Reading chronicle events grouped by phase
- Interpreting the quality score against the 0.95 threshold

Run this script from the repository root:
    python examples/pipeline_enterprise.py
"""

import asyncio
import logging
from pathlib import Path

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext
from gaia.pipeline.recursive_template import get_recursive_template

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
TEMPLATE_NAME = "enterprise"


# ---------------------------------------------------------------------------
# Template inspection helper
# ---------------------------------------------------------------------------


def inspect_template(template_name: str) -> None:
    """
    Print a detailed breakdown of a RecursivePipelineTemplate.

    This is useful for understanding what the template configures before
    committing to a pipeline run — especially when comparing 'enterprise'
    vs 'generic' to justify the additional overhead.
    """
    template = get_recursive_template(template_name)

    print(f"Template        : {template.name}")
    print(f"Description     : {template.description}")
    print(f"Quality threshold: {template.quality_threshold:.0%}")
    print(f"Max iterations  : {template.max_iterations}")

    print("\nAgent categories (agents assigned per phase):")
    for category, agents in template.agent_categories.items():
        agent_list = ", ".join(agents) if agents else "(none)"
        print(f"    {category:<14} -> {agent_list}")

    print("\nRouting rules:")
    if template.routing_rules:
        for rule in sorted(template.routing_rules, key=lambda r: r.priority):
            loop_flag = " [loop-back]" if rule.loop_back else ""
            guidance = f' — "{rule.guidance}"' if rule.guidance else ""
            print(
                f"    priority={rule.priority}  if {rule.condition!r}"
                f"  -> {rule.route_to}{loop_flag}{guidance}"
            )
    else:
        print("    (no routing rules)")

    print("\nQuality weights:")
    for dimension, weight in sorted(template.quality_weights.items()):
        bar = "#" * int(weight * 20)
        print(f"    {dimension:<25} {weight:.2f}  [{bar:<20}]")

    print("\nPhase configuration:")
    for phase in template.phases:
        agents = ", ".join(phase.agents) if phase.agents else "(none pre-assigned)"
        print(
            f"    {phase.name:<12} "
            f"category={phase.category.value:<12} "
            f"mode={phase.selection_mode.value:<12} "
            f"agents=[{agents}]"
        )


# ---------------------------------------------------------------------------
# Chronicle analysis helper
# ---------------------------------------------------------------------------


def print_chronicle_by_phase(chronicle: list) -> None:
    """
    Group and display chronicle events by pipeline phase.

    The chronicle is an ordered list of event dicts.  Each entry has at
    minimum an 'event' key and a 'timestamp'.  State transitions also carry
    'from_state' and 'to_state'; phase-scoped events carry 'phase'.
    """
    if not chronicle:
        print("  (no events recorded)")
        return

    # Group events by phase.  Events without a 'phase' key are filed under
    # a synthetic '_lifecycle_' bucket.
    phase_buckets: dict = {}
    for entry in chronicle:
        phase_key = entry.get("phase") or "_lifecycle_"
        phase_buckets.setdefault(phase_key, []).append(entry)

    for phase_key, events in phase_buckets.items():
        header = f"Phase: {phase_key}" if phase_key != "_lifecycle_" else "Lifecycle events"
        print(f"\n  {header} ({len(events)} events):")
        for evt in events:
            event_name = evt.get("event", "UNKNOWN")
            ts = evt.get("timestamp", "")[:23]  # trim microseconds for readability

            # State transitions have from/to fields; print them compactly.
            if "from_state" in evt and "to_state" in evt:
                detail = f"{evt['from_state']} -> {evt['to_state']}"
                if evt.get("reason"):
                    detail += f' ("{evt["reason"]}")'
            elif "data" in evt and evt["data"]:
                detail = str(evt["data"])[:60]
            else:
                detail = ""

            detail_str = f"  {detail}" if detail else ""
            print(f"    [{ts}] {event_name}{detail_str}")


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def run_enterprise() -> None:
    """Run an enterprise pipeline and produce a detailed analysis report."""

    # ------------------------------------------------------------------
    # Step 1: Inspect the enterprise template before running.
    # ------------------------------------------------------------------
    print("=" * 65)
    print("ENTERPRISE TEMPLATE CONFIGURATION")
    print("=" * 65)
    inspect_template(TEMPLATE_NAME)
    print()

    # ------------------------------------------------------------------
    # Step 2: Compare with the generic template to highlight differences.
    # ------------------------------------------------------------------
    generic = get_recursive_template("generic")
    enterprise = get_recursive_template("enterprise")

    print("=" * 65)
    print("GENERIC vs ENTERPRISE COMPARISON")
    print("=" * 65)
    print(f"  Quality threshold : generic={generic.quality_threshold:.0%}  "
          f"enterprise={enterprise.quality_threshold:.0%}")
    print(f"  Max iterations    : generic={generic.max_iterations}  "
          f"enterprise={enterprise.max_iterations}")

    for phase_name in ["planning", "quality"]:
        g_agents = generic.agent_categories.get(phase_name, [])
        e_agents = enterprise.agent_categories.get(phase_name, [])
        extra = [a for a in e_agents if a not in g_agents]
        if extra:
            print(f"  Extra {phase_name} agents: {', '.join(extra)}")
    print()

    # ------------------------------------------------------------------
    # Step 3: Build the PipelineContext for an enterprise-grade task.
    #
    # Note: We pass quality_threshold=0.95 on the context to match the
    # template's threshold.  The engine also reads "template" from the
    # config dict to load the template's routing rules and agent lists.
    # ------------------------------------------------------------------
    context = PipelineContext(
        pipeline_id="enterprise-001",
        user_goal=(
            "Implement a secure payment processing microservice with "
            "PCI-DSS compliance, comprehensive test coverage, and "
            "performance SLA of <100ms p99 latency"
        ),
        quality_threshold=0.95,
        max_iterations=15,
        concurrent_loops=5,
    )

    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=True,
        log_level=logging.WARNING,
        max_concurrent_loops=100,
        worker_pool_size=4,
    )

    # ------------------------------------------------------------------
    # Step 4: Initialize and run the enterprise pipeline.
    # ------------------------------------------------------------------
    print("=" * 65)
    print("ENTERPRISE PIPELINE EXECUTION")
    print("=" * 65)
    print(f"Pipeline ID : {context.pipeline_id}")
    print(f"Goal        : {context.user_goal[:80]}...")
    print(f"Threshold   : {context.quality_threshold:.0%}")
    print(f"Max iters   : {context.max_iterations}")
    print()

    await engine.initialize(
        context,
        config={
            "template": TEMPLATE_NAME,
            "enable_hooks": True,
        },
    )

    snapshot = await engine.start()

    # ------------------------------------------------------------------
    # Step 5: Detailed artifact inspection.
    # ------------------------------------------------------------------
    print("=" * 65)
    print("ARTIFACTS PRODUCED")
    print("=" * 65)
    if snapshot.artifacts:
        for artifact_key, artifact_value in snapshot.artifacts.items():
            if isinstance(artifact_value, dict):
                print(f"\n  [{artifact_key}]  (dict, {len(artifact_value)} keys)")
                for k, v in artifact_value.items():
                    v_repr = repr(v)[:70] if not isinstance(v, (list, dict)) else (
                        f"<list len={len(v)}>" if isinstance(v, list)
                        else f"<dict keys={list(v.keys())[:4]}>"
                    )
                    print(f"      {k}: {v_repr}")
            elif isinstance(artifact_value, str):
                print(f"\n  [{artifact_key}]  \"{artifact_value[:120]}\"")
            else:
                print(f"\n  [{artifact_key}]  {repr(artifact_value)[:120]}")
    else:
        print("  No artifacts were produced.")

    # ------------------------------------------------------------------
    # Step 6: Quality score with enterprise threshold interpretation.
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print("QUALITY EVALUATION")
    print("=" * 65)
    if snapshot.quality_score is not None:
        score = snapshot.quality_score
        threshold = context.quality_threshold
        passed = score >= threshold
        gap = score - threshold
        print(f"  Score     : {score:.4f} ({score:.1%})")
        print(f"  Threshold : {threshold:.4f} ({threshold:.1%})")
        print(f"  Gap       : {gap:+.4f}  ({'PASS' if passed else 'FAIL'})")
        print(f"  Iterations: {snapshot.iteration_count}")
    else:
        print("  Quality evaluation did not complete (pipeline may have failed earlier).")

    if snapshot.defects:
        print(f"\n  Defects ({len(snapshot.defects)}):")
        for defect in snapshot.defects[:5]:
            desc = defect.get("description", str(defect))[:80]
            severity = defect.get("severity", "unknown")
            print(f"    [{severity}] {desc}")
        if len(snapshot.defects) > 5:
            print(f"    ... and {len(snapshot.defects) - 5} more")

    # ------------------------------------------------------------------
    # Step 7: Chronicle events grouped by phase.
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print("CHRONICLE EVENTS BY PHASE")
    print("=" * 65)
    chronicle = engine.get_chronicle()
    print_chronicle_by_phase(chronicle)

    # ------------------------------------------------------------------
    # Step 8: Final state summary.
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print("FINAL STATE")
    print("=" * 65)
    print(f"  State     : {snapshot.state.name}")
    elapsed = snapshot.elapsed_time()
    print(f"  Elapsed   : {elapsed:.2f}s" if elapsed is not None else "  Elapsed   : N/A")
    if snapshot.error_message:
        print(f"  Error     : {snapshot.error_message}")

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")

    engine.shutdown()
    print("\nEnterprise pipeline complete. Engine shut down.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_enterprise())
