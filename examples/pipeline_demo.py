#!/usr/bin/env python3
"""
GAIA Pipeline Demo

Demonstrates the pipeline system with selectable templates and optional stub mode
for offline/CI use.

Run from the repository root:
    python examples/pipeline_demo.py --goal "Build a REST API"
    python examples/pipeline_demo.py --goal "Prototype a chat bot" --template rapid --stub
    python examples/pipeline_demo.py --goal "Production service" --template enterprise --verbose
"""

import argparse
import asyncio
import logging
from pathlib import Path

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.recursive_template import RECURSIVE_TEMPLATES, get_recursive_template
from gaia.pipeline.state import PipelineContext

AGENTS_DIR = Path(__file__).parent.parent / "config" / "agents"

PHASES = ["PLANNING", "DEVELOPMENT", "QUALITY", "DECISION"]


async def run_demo(
    goal: str,
    template: str = "generic",
    model: str = "Qwen3-0.6B-GGUF",
    verbose: bool = False,
    stub: bool = False,
) -> dict:
    """
    Run the pipeline demo and return a result summary dict.

    Args:
        goal: Natural language goal for the pipeline.
        template: Template name — generic, rapid, or enterprise.
        model: Lemonade model ID to use for agent execution.
        verbose: Enable INFO-level logging from the engine.
        stub: When True, skip real LLM connectivity and use stub agent execution.

    Returns:
        Dict containing state, quality_score, iteration_count, artifacts,
        defects, elapsed_time, and agents_used.
    """
    if stub:
        print("WARNING: stub mode active — agents return placeholder text (no real LLM).")
    else:
        print(f"Connecting to Lemonade backend with model: {model}")

    tmpl = get_recursive_template(template)
    log_level = logging.INFO if verbose else logging.WARNING

    print(f"Pipeline goal     : {goal}")
    print(f"Template          : {tmpl.name}  ({tmpl.description})")
    print(f"Quality threshold : {tmpl.quality_threshold:.0%}")
    print(f"Max iterations    : {tmpl.max_iterations}")
    print(f"Agents dir        : {AGENTS_DIR}")
    print()

    context = PipelineContext(
        pipeline_id=f"demo-{template}-001",
        user_goal=goal,
        quality_threshold=tmpl.quality_threshold,
        max_iterations=tmpl.max_iterations,
        concurrent_loops=5,
    )

    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=True,
        log_level=log_level,
        max_concurrent_loops=100,
        worker_pool_size=4,
        model_id=None if stub else model,
        skip_lemonade=stub,
    )

    await engine.initialize(
        context,
        config={
            "template": template,
            "enable_hooks": True,
        },
    )

    print("Starting pipeline execution...")
    print(f"Phases: {' -> '.join(PHASES)}")
    print()

    snapshot = await engine.start()

    print("\n" + "=" * 60)
    print("PIPELINE RESULT SUMMARY")
    print("=" * 60)
    print(f"Final state       : {snapshot.state.name}")
    print(f"Last phase        : {snapshot.current_phase or 'N/A'}")
    print(f"Iterations run    : {snapshot.iteration_count}")

    if snapshot.quality_score is not None:
        passed = snapshot.quality_score >= context.quality_threshold
        verdict = "PASS" if passed else "FAIL"
        print(f"Quality score     : {snapshot.quality_score:.3f}  [{verdict}]")
    else:
        print("Quality score     : not evaluated")

    elapsed = snapshot.elapsed_time() if hasattr(snapshot, "elapsed_time") else None
    if elapsed is not None:
        print(f"Elapsed time      : {elapsed:.2f}s")
    else:
        print("Elapsed time      : N/A")

    agents_used = list(tmpl.agent_categories.values())
    agents_flat = [a for category in agents_used for a in category]
    print(f"Agents used       : {', '.join(agents_flat) if agents_flat else 'none'}")

    if snapshot.artifacts:
        print(f"Artifacts ({len(snapshot.artifacts)}):")
        for key, value in snapshot.artifacts.items():
            value_repr = (
                f"<dict with {len(value)} keys>"
                if isinstance(value, dict)
                else repr(value)[:80]
            )
            print(f"    {key}: {value_repr}")
    else:
        print("Artifacts         : none")

    print(f"Defects found     : {len(snapshot.defects)}")
    print(f"Chronicle events  : {len(snapshot.chronicle)}")

    if snapshot.error_message:
        print(f"\nERROR: {snapshot.error_message}")

    print("=" * 60)

    engine.shutdown()
    print("\nEngine shut down cleanly.")

    return {
        "state": snapshot.state.name,
        "quality_score": snapshot.quality_score,
        "iteration_count": snapshot.iteration_count,
        "artifacts": snapshot.artifacts,
        "defects": snapshot.defects,
        "elapsed_time": elapsed,
        "agents_used": agents_flat,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GAIA Pipeline Demo")
    parser.add_argument("--goal", required=True, help="Natural language goal for the pipeline")
    parser.add_argument(
        "--template",
        choices=list(RECURSIVE_TEMPLATES.keys()),
        default="generic",
        help="Pipeline template to use (default: generic)",
    )
    parser.add_argument(
        "--model",
        default="Qwen3-0.6B-GGUF",
        help="Lemonade model ID (default: Qwen3-0.6B-GGUF)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose engine logging",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Run in stub mode (no real LLM, for offline/CI use)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_demo(
            goal=args.goal,
            template=args.template,
            model=args.model,
            verbose=args.verbose,
            stub=args.stub,
        )
    )
