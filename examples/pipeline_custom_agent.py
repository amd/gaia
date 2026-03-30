"""
GAIA Pipeline — Custom Agent Registration
==========================================

Demonstrates how to programmatically build an AgentDefinition (without a YAML
file) and register it in the AgentRegistry for use in a pipeline run.

This pattern is useful for:
- Embedding agent logic directly in code during prototyping
- Injecting test doubles or mock agents in integration tests
- Extending the agent ecosystem at runtime (e.g., plugin architectures)
- Registering agents whose definitions live in a database or remote store

Classes used (from src/gaia/agents/base/context.py, exported via __init__.py):
    AgentDefinition    — complete agent description (id, name, category, etc.)
    AgentCapabilities  — list of capability strings + tool names
    AgentTriggers      — phase/keyword/complexity activation conditions
    AgentConstraints   — guardrails (max files, timeout, review flag)

Registry API (from src/gaia/agents/registry.py):
    AgentRegistry.register_agent(definition)  — adds to the live registry
    AgentRegistry.get_agent(agent_id)         — retrieve by ID
    AgentRegistry.get_statistics()            — total/enabled/categories
    AgentRegistry.select_agent(...)           — capability-based routing
    AgentRegistry.unregister_agent(agent_id) — remove by ID

Run this script from the repository root:
    python examples/pipeline_custom_agent.py
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from gaia.agents.registry import AgentRegistry
from gaia.agents.base import (
    AgentDefinition,
    AgentCapabilities,
    AgentTriggers,
    AgentConstraints,
)
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext

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
# Custom agent definition builder
# ---------------------------------------------------------------------------


def build_ml_pipeline_agent() -> AgentDefinition:
    """
    Build an AgentDefinition for a Machine Learning Pipeline Engineer
    without using a YAML file.

    Every field maps directly to what a YAML file would contain.
    The constructor mirrors the AgentDefinition dataclass fields exactly.
    """
    return AgentDefinition(
        # id must be unique within the registry; use kebab-case convention.
        id="ml-pipeline-engineer",

        # name is the human-readable display label.
        name="Machine Learning Pipeline Engineer",

        # version follows semver; bump when the system_prompt changes
        # in a backward-incompatible way.
        version="1.0.0",

        # category maps to AgentRegistry.AGENT_CATEGORIES keys:
        # 'planning', 'development', 'review', 'management'.
        category="development",

        description=(
            "Designs and implements end-to-end ML pipelines including "
            "data ingestion, feature engineering, model training, "
            "evaluation, and production serving infrastructure."
        ),

        # AgentCapabilities: the 'capabilities' list drives capability-based
        # routing in AgentRegistry.select_agent() via the capability index.
        capabilities=AgentCapabilities(
            capabilities=[
                "machine-learning",
                "python-development",
                "data-pipeline-design",
                "model-serving",
                "mlflow-integration",
                "feature-engineering",
            ],
            tools=[
                "file_read",
                "file_write",
                "run_python",
                "search_codebase",
                "install_package",
            ],
            execution_targets={"default": "cpu", "training": "gpu"},
        ),

        # AgentTriggers: determine when/where this agent is selected.
        # phases must match the PipelinePhase constants (all uppercase).
        # keywords are matched (case-insensitive) against the task description.
        # complexity_range: (min, max) on a 0.0–1.0 scale; this agent
        # handles mid-to-high complexity ML tasks.
        triggers=AgentTriggers(
            keywords=[
                "machine learning",
                "ml pipeline",
                "model training",
                "feature engineering",
                "neural network",
                "deep learning",
                "mlflow",
                "data science",
            ],
            phases=["PLANNING", "DEVELOPMENT"],
            complexity_range=(0.5, 1.0),
        ),

        # system_prompt is typically a path to a markdown file or an inline
        # string.  For programmatic agents we use an inline prompt.
        system_prompt=(
            "You are an expert Machine Learning Pipeline Engineer. "
            "Design robust, reproducible ML pipelines following MLOps best practices. "
            "Prioritize reproducibility, monitoring, and model versioning."
        ),

        tools=[
            "file_read",
            "file_write",
            "run_python",
            "search_codebase",
            "install_package",
        ],

        # AgentConstraints: execution guardrails.
        constraints=AgentConstraints(
            max_file_changes=30,
            max_lines_per_file=600,
            requires_review=True,
            timeout_seconds=600,
            max_steps=150,
        ),

        metadata={
            "author": "Example Script",
            "tags": ["ml", "python", "mlops", "pipeline"],
            "specialization": "MLOps and ML pipeline engineering",
        },

        enabled=True,
    )


# ---------------------------------------------------------------------------
# Registry inspection helpers
# ---------------------------------------------------------------------------


def print_agent_info(agent: Optional[AgentDefinition], label: str = "") -> None:
    """Print a formatted view of an AgentDefinition."""
    prefix = f"[{label}] " if label else ""
    if agent is None:
        print(f"{prefix}  (not found)")
        return

    print(f"{prefix}Name       : {agent.name}")
    print(f"{prefix}ID         : {agent.id}")
    print(f"{prefix}Version    : {agent.version}")
    print(f"{prefix}Category   : {agent.category}")
    print(f"{prefix}Enabled    : {agent.enabled}")
    print(f"{prefix}Description: {agent.description.strip()[:90]}")

    caps = agent.capabilities.capabilities if agent.capabilities else []
    print(f"{prefix}Capabilities: {', '.join(caps[:6])}")

    if agent.triggers:
        print(f"{prefix}Phases     : {', '.join(agent.triggers.phases)}")
        print(f"{prefix}Keywords   : {', '.join(agent.triggers.keywords[:5])}")
        lo, hi = agent.triggers.complexity_range
        print(f"{prefix}Complexity : {lo:.1f} – {hi:.1f}")

    if agent.constraints:
        print(
            f"{prefix}Constraints: "
            f"max_files={agent.constraints.max_file_changes}, "
            f"timeout={agent.constraints.timeout_seconds}s"
        )


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def demo_custom_agent() -> None:
    """Build, register, and use a custom agent definition."""

    # ------------------------------------------------------------------
    # Step 1: Build the custom AgentDefinition programmatically.
    # ------------------------------------------------------------------
    custom_agent = build_ml_pipeline_agent()

    print("=" * 65)
    print("CUSTOM AGENT DEFINITION (programmatically built)")
    print("=" * 65)
    print_agent_info(custom_agent)
    print()

    # Validate it can be serialized (to_dict mirrors the YAML structure).
    agent_dict = custom_agent.to_dict()
    print(f"to_dict() keys: {list(agent_dict.keys())}")
    print()

    # ------------------------------------------------------------------
    # Step 2: Create a standalone AgentRegistry and load YAML agents.
    # ------------------------------------------------------------------
    registry = AgentRegistry(
        agents_dir=str(AGENTS_DIR),
        auto_reload=False,
        max_concurrent_loads=5,
    )
    await registry.initialize()

    stats_before = registry.get_statistics()
    print("=" * 65)
    print("REGISTRY STATISTICS (before custom agent registration)")
    print("=" * 65)
    print(f"Total agents   : {stats_before['total_agents']}")
    print(f"Enabled agents : {stats_before['enabled_agents']}")
    print(f"Categories     : {stats_before['categories']}")
    print()

    # ------------------------------------------------------------------
    # Step 3: Register the custom agent.
    #
    # register_agent() is synchronous (it uses _run_async internally).
    # It adds the definition to _agents, rebuilds all indexes
    # (capability, trigger, category), and invalidates the LRU cache.
    # ------------------------------------------------------------------
    registry.register_agent(custom_agent)

    stats_after = registry.get_statistics()
    print("=" * 65)
    print("REGISTRY STATISTICS (after custom agent registration)")
    print("=" * 65)
    print(f"Total agents   : {stats_after['total_agents']}")
    print(f"Enabled agents : {stats_after['enabled_agents']}")
    print(f"Categories     : {stats_after['categories']}")

    # Show the delta: how the custom agent changed the counts.
    added = stats_after['total_agents'] - stats_before['total_agents']
    print(f"  -> {added} new agent(s) registered")
    print()

    # ------------------------------------------------------------------
    # Step 4: Retrieve the custom agent by ID to confirm registration.
    # ------------------------------------------------------------------
    retrieved = registry.get_agent("ml-pipeline-engineer")

    print("=" * 65)
    print("RETRIEVED AGENT (confirmed registered)")
    print("=" * 65)
    print_agent_info(retrieved, label="retrieved")
    print()

    # ------------------------------------------------------------------
    # Step 5: Demonstrate that select_agent() can route to the custom agent.
    #
    # select_agent() ranks agents by keyword overlap and phase match.
    # Our custom agent declares 'DEVELOPMENT' in its trigger phases and
    # 'machine learning' as a keyword, so a task mentioning ML should
    # select it over a generic senior-developer.
    # ------------------------------------------------------------------
    print("=" * 65)
    print("AGENT SELECTION — custom agent vs. generic agents")
    print("=" * 65)

    ml_task = (
        "Train a neural network model for time-series forecasting "
        "and deploy it as an ML pipeline with MLflow tracking"
    )

    selected_id = registry.select_agent(
        task_description=ml_task,
        current_phase="DEVELOPMENT",
        state={"complexity": 0.8},
    )

    print(f"Task    : {ml_task[:80]}")
    print(f"Phase   : DEVELOPMENT")
    print(f"Selected: {selected_id}")

    if selected_id == "ml-pipeline-engineer":
        print("  -> Custom agent was correctly selected for the ML task.")
    elif selected_id:
        print(f"  -> Registry selected '{selected_id}' (custom agent competing with YAML agents).")
    else:
        print("  -> No agent selected (check that agents are loaded and phases match).")
    print()

    # Also demonstrate capability-based lookup.
    ml_capable = registry.get_agents_by_capability("machine-learning")
    print(f"Agents with 'machine-learning' capability: {[a.id for a in ml_capable]}")
    print()

    # ------------------------------------------------------------------
    # Step 6: Use the custom agent in a full pipeline run.
    #
    # We pass the registry's agents_dir so the engine loads the same YAML
    # agents.  Then we explicitly override the PLANNING agents list in the
    # config to include our custom agent by ID.
    # ------------------------------------------------------------------
    print("=" * 65)
    print("PIPELINE RUN WITH CUSTOM AGENT")
    print("=" * 65)

    context = PipelineContext(
        pipeline_id="custom-agent-demo-001",
        user_goal=(
            "Build an ML pipeline for customer churn prediction "
            "with feature engineering and model serving via REST API"
        ),
        quality_threshold=0.85,
        max_iterations=5,
    )

    engine = PipelineEngine(
        agents_dir=str(AGENTS_DIR),
        enable_logging=True,
        log_level=logging.WARNING,
        max_concurrent_loops=50,
        worker_pool_size=4,
    )

    await engine.initialize(
        context,
        config={
            "template": "generic",
            "enable_hooks": True,
        },
    )

    # Register the custom agent on the engine's already-initialized registry
    # so it participates in the pipeline's agent selection for this run.
    if engine._agent_registry is not None:
        engine._agent_registry.register_agent(custom_agent)
        post_reg_stats = engine._agent_registry.get_statistics()
        print(
            f"Registered custom agent in engine's registry. "
            f"Total agents: {post_reg_stats['total_agents']}"
        )

    snapshot = await engine.start()

    print(f"\nPipeline result: {snapshot.state.name}")
    print(f"Quality score  : {snapshot.quality_score:.3f}" if snapshot.quality_score else
          "Quality score  : N/A")
    print(f"Artifacts      : {list(snapshot.artifacts.keys())}")

    # Check if our custom agent was selected during the run.
    planning_agent_used = snapshot.artifacts.get("planning_agent")
    if planning_agent_used:
        print(f"Planning agent  : {planning_agent_used}")
        if planning_agent_used == "ml-pipeline-engineer":
            print("  -> Custom ML agent was selected for the planning phase.")

    # ------------------------------------------------------------------
    # Step 7: Unregister the custom agent to show the removal API.
    # ------------------------------------------------------------------
    removed = registry.unregister_agent("ml-pipeline-engineer")
    final_stats = registry.get_statistics()
    print(f"\nUnregistered custom agent: {removed}")
    print(f"Agents after removal: {final_stats['total_agents']}")

    if HAS_METRICS:
        print("\n[metrics] MetricsCollector is available in this build.")
    else:
        print("\n[metrics] gaia.pipeline.metrics not installed — skipping metrics.")

    engine.shutdown()
    registry.shutdown()
    print("\nCustom agent demo complete. All resources shut down.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(demo_custom_agent())
