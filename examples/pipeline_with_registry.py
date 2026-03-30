"""
GAIA Pipeline — Agent Registry Inspection
==========================================

Demonstrates how to use AgentRegistry independently of the PipelineEngine to:
- Load agent definitions from YAML files in config/agents/
- Read registry statistics (agent count per category)
- Select agents for specific pipeline phases using capability-based routing
- Inspect the full AgentDefinition fields (name, capabilities, triggers)
- Print a formatted summary of registered agents

This is useful for:
- Auditing which agents are available before running a pipeline
- Debugging agent selection logic
- Building tooling that reports on the agent ecosystem

Run this script from the repository root:
    python examples/pipeline_with_registry.py
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from gaia.agents.registry import AgentRegistry
from gaia.agents.base import AgentDefinition

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

# Pipeline phases we want to demonstrate agent selection for.
DEMO_PHASES = ["PLANNING", "DEVELOPMENT", "QUALITY"]

# Example tasks representative of each phase.
DEMO_TASKS = {
    "PLANNING": "Analyze requirements and create an architecture plan for the feature",
    "DEVELOPMENT": "Implement the backend API endpoints with full-stack development",
    "QUALITY": "Review code quality, run tests, and audit for security vulnerabilities",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _fmt_list(items: list, max_items: int = 5) -> str:
    """Format a list for printing, truncating if too long."""
    if not items:
        return "(none)"
    visible = items[:max_items]
    suffix = f" ... +{len(items) - max_items} more" if len(items) > max_items else ""
    return ", ".join(visible) + suffix


def _print_agent_card(agent: AgentDefinition) -> None:
    """Print a formatted card for a single AgentDefinition."""
    print(f"    Name        : {agent.name}")
    print(f"    ID          : {agent.id}")
    print(f"    Version     : {agent.version}")
    print(f"    Category    : {agent.category}")
    print(f"    Enabled     : {agent.enabled}")
    print(f"    Description : {agent.description.strip()[:100]}")

    # AgentCapabilities: the core list of what this agent can do.
    caps = agent.capabilities.capabilities if agent.capabilities else []
    print(f"    Capabilities: {_fmt_list(caps)}")

    # AgentTriggers: when/where the agent activates.
    if agent.triggers:
        phases = agent.triggers.phases
        keywords = agent.triggers.keywords
        complexity_min, complexity_max = agent.triggers.complexity_range
        print(f"    Phases      : {_fmt_list(phases)}")
        print(f"    Keywords    : {_fmt_list(keywords)}")
        print(f"    Complexity  : {complexity_min:.1f} – {complexity_max:.1f}")

    # AgentConstraints: execution guardrails.
    if agent.constraints:
        print(
            f"    Constraints : "
            f"max_files={agent.constraints.max_file_changes}, "
            f"timeout={agent.constraints.timeout_seconds}s, "
            f"review_required={agent.constraints.requires_review}"
        )


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def inspect_registry() -> None:
    """Load the registry and print a comprehensive inspection report."""

    # ------------------------------------------------------------------
    # Step 1: Create and initialize the registry.
    #
    # AgentRegistry can be used standalone — it does not require a
    # PipelineEngine.  auto_reload=False disables the watchdog file
    # watcher, which is unnecessary for a read-only inspection script.
    # ------------------------------------------------------------------
    print(f"Loading agents from: {AGENTS_DIR}")
    print()

    registry = AgentRegistry(
        agents_dir=str(AGENTS_DIR),
        auto_reload=False,      # No need for hot-reload in a one-shot script.
        max_concurrent_loads=5,
    )

    await registry.initialize()

    # ------------------------------------------------------------------
    # Step 2: Print high-level statistics.
    #
    # get_statistics() returns a dict with:
    #   total_agents      — all loaded definitions
    #   enabled_agents    — subset with enabled=True
    #   categories        — {category_name: count} from the category index
    #   capabilities      — number of distinct capability strings
    #   trigger_keywords  — number of distinct keyword strings
    # ------------------------------------------------------------------
    stats = registry.get_statistics()

    print("=" * 60)
    print("REGISTRY STATISTICS")
    print("=" * 60)
    print(f"Total agents loaded : {stats['total_agents']}")
    print(f"Enabled agents      : {stats['enabled_agents']}")
    print(f"Distinct capabilities: {stats['capabilities']}")
    print(f"Trigger keywords    : {stats['trigger_keywords']}")
    print()

    # Categories is a dict mapping category name -> count of agents in it.
    if stats["categories"]:
        print("Agents per category:")
        for category, count in sorted(stats["categories"].items()):
            print(f"    {category:<20} {count} agent(s)")
    else:
        print("No agents indexed by category yet.")
    print()

    # ------------------------------------------------------------------
    # Step 3: Demonstrate select_agent() for each demo phase.
    #
    # select_agent() applies a multi-stage scoring algorithm:
    #   1. Filter by required_capabilities (if supplied)
    #   2. Filter by phase — agents with matching triggers.phases get priority
    #   3. Filter by complexity range
    #   4. Score by keyword overlap with the task description
    #   5. Return the highest-scoring agent ID
    # ------------------------------------------------------------------
    print("=" * 60)
    print("AGENT SELECTION DEMO")
    print("=" * 60)

    for phase in DEMO_PHASES:
        task = DEMO_TASKS[phase]
        print(f"\nPhase: {phase}")
        print(f"  Task: {task[:80]}")

        # state dict may carry complexity (0.0–1.0) and other context.
        state = {"complexity": 0.6, "iteration": 1}

        selected_id: Optional[str] = registry.select_agent(
            task_description=task,
            current_phase=phase,
            state=state,
        )

        if selected_id:
            agent = registry.get_agent(selected_id)
            print(f"  Selected: {selected_id}")
            if agent:
                _print_agent_card(agent)
        else:
            print("  Selected: (no matching agent found)")

    # ------------------------------------------------------------------
    # Step 4: Print a full summary of all loaded agents.
    # ------------------------------------------------------------------
    all_agents = registry.get_all_agents()

    if all_agents:
        print()
        print("=" * 60)
        print(f"ALL REGISTERED AGENTS ({len(all_agents)})")
        print("=" * 60)

        for agent_id, agent in sorted(all_agents.items()):
            print(f"\n  [{agent_id}]")
            _print_agent_card(agent)
    else:
        print("\nNo agents are registered. Check that AGENTS_DIR contains .yaml files.")

    # ------------------------------------------------------------------
    # Step 5: Demonstrate get_agents_by_category().
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("AGENTS BY CATEGORY")
    print("=" * 60)

    for category in ["planning", "development", "review", "management"]:
        agents_in_cat = registry.get_agents_by_category(category)
        if agents_in_cat:
            names = [a.name for a in agents_in_cat]
            print(f"  {category:<14}: {_fmt_list(names)}")
        else:
            print(f"  {category:<14}: (none loaded)")

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")

    registry.shutdown()
    print("\nRegistry shut down cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(inspect_registry())
