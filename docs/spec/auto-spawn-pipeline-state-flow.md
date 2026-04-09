---
title: Auto-Spawn Pipeline State Flow Specification
description: Specification for the auto-spawn pipeline state machine including stage-by-stage data flow, state transitions, hook trigger points, and ComponentLoader interactions.
status: Published
---

# Auto-Spawn Pipeline State Flow Specification

**Version:** 1.0.0
**Date:** 2026-04-08
**Author:** Jordan Blake, Principal Software Engineer & Technical Lead
**Status:** Proposed

---

## Overview

This document specifies the state flow for GAIA's auto-spawn capable pipeline, including:
- Stage-by-stage data flow
- State transitions
- Hook trigger points
- ComponentLoader interactions
- Gap detection and agent spawning

---

## Pipeline State Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AUTO-SPAWN PIPELINE STATE MACHINE                    │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │ INITIALIZING │ ◄────────────────────────────────────┐
    └──────┬───────┘                                      │
           │ config.valid()                               │ restart()
           ▼                                                │
    ┌──────────────┐                                       │
    │    READY     │                                       │
    └──────┬───────┘                                       │
           │ start()                                        │
           ▼                                                │
    ┌──────────────┐                                       │
    │    RUNNING   │───────────────────────────────────────┤
    └──────┬───────┘                                       │
           │                                                │
           ├────────────────────────────────────────────────┤
           │                                                │
           ▼ (Stage 1)                                      │
    ┌─────────────────────────────────────────────────────┐ │
    │           DOMAIN_ANALYZER_STAGE                      │ │
    │  ┌─────────────────────────────────────────────┐    │ │
    │  │ Input: task_description (string)            │    │ │
    │  │ Output: domain_blueprint (dict)             │    │ │
    │  │                                             │    │ │
    │  │ Hooks:                                      │    │ │
    │  │   - on_enter: Log stage entry               │    │ │
    │  │   - on_exit: Store domain_blueprint         │    │ │
    │  │   - on_error: Transition to FAILED          │    │ │
    │  │                                             │    │ │
    │  │ ComponentLoader:                            │    │ │
    │  │   - READ: checklists/domain-analysis.md     │    │ │
    │  │   - WRITE: knowledge/domain-knowledge.md    │    │ │
    │  └─────────────────────────────────────────────┘    │ │
    └─────────────────────────────────────────────────────┘ │
           │                                                │
           ▼ (Stage 2)                                      │
    ┌─────────────────────────────────────────────────────┐ │
    │           WORKFLOW_MODELER_STAGE                     │ │
    │  ┌─────────────────────────────────────────────┐    │ │
    │  │ Input: domain_blueprint (dict)              │    │ │
    │  │ Output: workflow_model (dict)               │    │ │
    │  │                                             │    │ │
    │  │ Hooks:                                      │    │ │
    │  │   - on_enter: Load workflow templates       │    │ │
    │  │   - on_exit: Store workflow_model           │    │ │
    │  │   - on_error: Transition to FAILED          │    │ │
    │  │                                             │    │ │
    │  │ ComponentLoader:                            │    │ │
    │  │   - READ: workflows/*.md                    │    │ │
    │  │   - WRITE: tasks/task-breakdown.md          │    │ │
    │  └─────────────────────────────────────────────┘    │ │
    └─────────────────────────────────────────────────────┘ │
           │                                                │
           ▼ (Stage 3)                                      │
    ┌─────────────────────────────────────────────────────┐ │
    │           LOOM_BUILDER_STAGE                         │ │
    │  ┌─────────────────────────────────────────────┐    │ │
    │  │ Input: workflow_model (dict)                │    │ │
    │  │         domain_blueprint (dict)             │    │ │
    │  │ Output: loom_topology (dict)                │    │ │
    │  │                                             │    │ │
    │  │ Hooks:                                      │    │ │
    │  │   - on_enter: Load agent registry           │    │ │
    │  │   - on_exit: Store loom_topology            │    │ │
    │  │   - on_error: Transition to FAILED          │    │ │
    │  │                                             │    │ │
    │  │ ComponentLoader:                            │    │ │
    │  │   - READ: agents/*.md (capability lookup)   │    │ │
    │  │   - WRITE: documents/loom-topology.md       │    │ │
    │  └─────────────────────────────────────────────┘    │ │
    └─────────────────────────────────────────────────────┘ │
           │                                                │
           ▼ (Stage 4)                                      │
    ┌─────────────────────────────────────────────────────┐ │
    │           GAP_DETECTOR_STAGE                         │ │
    │  ┌─────────────────────────────────────────────┐    │ │
    │  │ Input: recommended_agents (list)            │    │ │
    │  │         task_objective (string)             │    │ │
    │  │ Output: gap_analysis (dict)                 │    │ │
    │  │                                             │    │ │
    │  │ Hooks:                                      │    │ │
    │  │   - on_enter: Scan agent filesystem         │    │ │
    │  │   - on_exit: Store gap_analysis             │    │ │
    │  │   - on_gap_found: TRIGGER_AGENT_SPAWN       │    │ │
    │  │   - on_error: Transition to FAILED          │    │ │
    │  │                                             │    │ │
    │  │ ComponentLoader:                            │    │ │
    │  │   - READ: agents/*.md (scan)                │    │ │
    │  │   - READ: .claude/agents/*.yml (scan)       │    │ │
    │  │   - WRITE: reports/gap-analysis.md          │    │ │
    │  └─────────────────────────────────────────────┘    │ │
    └─────────────────────────────────────────────────────┘ │
           │                                                │
           │ gap_analysis.gaps_identified?                  │
           ├──────────────────┬─────────────────────────────┤
           │ YES              │ NO                          │
           ▼                  ▼                             │
    ┌─────────────────┐  ┌─────────────────┐               │
    │ AGENT_SPAWN     │  │ PIPELINE_       │               │
    │     STAGE       │  │ EXECUTOR_STAGE  │               │
    │  ┌─────────────┐│  │  ┌─────────────┐│               │
    │  │ Input:      ││  │  │ Input:      ││               │
    │  │  - gaps     ││  │  │  - loom     ││               │
    │  │  - domain   ││  │  │  - blueprint││               │
    │  │ Output:     ││  │  │ Output:     ││               │
    │  │  - spawned  ││  │  │  - result   ││               │
    │  │             ││  │  │             ││               │
    │  │ Hooks:      ││  │  │ Hooks:      ││               │
    │  │  - on_spawn ││  │  │ on_enter    ││               │
    │  │  - on_done  ││  │  │ on_exit     ││               │
    │  │             ││  │  │ on_complete ││               │
    │  │ Component:  ││  │  │ Component:  ││               │
    │  │  - GENERATE ││  │  │ READ: loom  ││               │
    │  │    agents/* ││  │  │ WRITE:      ││               │
    │  │             ││  │  │   results/  ││               │
    │  └─────────────┘│  │  └─────────────┘│               │
    └────────┬────────┘  └────────┬────────┘               │
           │                  │                           │
           └──────────────────┴───────────────────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │  COMPLETED   │
                   └──────────────┘
```

---

## Stage Specifications

### Stage 1: Domain Analyzer

**Purpose:** Analyze input tasks to identify knowledge domains, requirements, and dependencies.

**Input Contract:**
```python
{
    "task_description": str,      # Natural language task
    "context": dict,              # Optional context (user info, project state)
}
```

**Output Contract:**
```python
{
    "domain_blueprint": {
        "primary_domain": str,
        "secondary_domains": list[str],
        "domain_requirements": dict,
        "domain_constraints": dict,
        "cross_domain_dependencies": list[dict],
        "confidence_score": float,
        "reasoning": str
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Log stage entry, increment iteration counter |
| `on_exit` | Stage completion | Store `domain_blueprint` in PipelineSnapshot.artifacts |
| `on_error` | Exception | Log error, transition to FAILED state |
| `on_quality_check` | After exit | Validate blueprint has required fields |

**ComponentLoader Interactions:**
```python
# READ: Checklist for validation
checklist = loader.load_component("checklists/domain-analysis-checklist.md")

# READ: Knowledge for reference
domain_knowledge = loader.load_component("knowledge/domain-knowledge.md")

# WRITE: Analysis results
loader.save_component(
    "knowledge/domain-analysis-result.md",
    content=formatted_blueprint,
    frontmatter={"template_type": "knowledge", "stage": "domain_analysis"}
)
```

---

### Stage 2: Workflow Modeler

**Purpose:** Design execution workflows based on domain analysis.

**Input Contract:**
```python
{
    "domain_blueprint": dict,    # From Stage 1
}
```

**Output Contract:**
```python
{
    "workflow_model": {
        "workflow_pattern": str,           # waterfall|agile|spiral|v-model|pipeline
        "phases": list[dict],
        "milestones": list[dict],
        "complexity_score": float,
        "recommended_agents": list[str],
        "reasoning": str
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Load workflow pattern templates |
| `on_exit` | Stage completion | Store `workflow_model` in PipelineSnapshot.artifacts |
| `on_pattern_select` | After pattern selection | Log selected pattern with rationale |
| `on_error` | Exception | Log error, transition to FAILED state |

**ComponentLoader Interactions:**
```python
# READ: Workflow templates
waterflow_tpl = loader.load_component("workflows/waterfall.md")
agile_tpl = loader.load_component("workflows/agile.md")

# WRITE: Task breakdown
loader.save_component(
    "tasks/task-breakdown.md",
    content=formatted_phases,
    frontmatter={"template_type": "tasks", "phase_count": len(phases)}
)
```

---

### Stage 3: Loom Builder

**Purpose:** Build agent execution topology from workflow model.

**Input Contract:**
```python
{
    "workflow_model": dict,     # From Stage 2
    "domain_blueprint": dict,   # From Stage 1
}
```

**Output Contract:**
```python
{
    "loom_topology": {
        "execution_graph": {
            "nodes": list[dict],
            "edges": list[dict],
            "entry_point": str,
            "exit_point": str
        },
        "agent_sequence": list[str],
        "component_bindings": dict[str, list[str]],
        "agent_configurations": dict[str, dict],
        "gaps_identified": dict,
        "reasoning": str
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Load agent registry from MD files |
| `on_exit` | Stage completion | Store `loom_topology` in PipelineSnapshot.artifacts |
| `on_agent_select` | Per agent selection | Log agent selection rationale |
| `on_error` | Exception | Log error, transition to FAILED state |

**ComponentLoader Interactions:**
```python
# READ: Agent definitions for capability lookup
for agent_md in loader.list_components("agents"):
    agent = loader.load_component(agent_md)
    capabilities = agent["frontmatter"].get("capabilities", [])

# WRITE: Loom topology document
loader.save_component(
    "documents/loom-topology.md",
    content=formatted_topology,
    frontmatter={
        "template_type": "documents",
        "agent_count": len(agent_sequence),
        "graph_nodes": len(execution_graph["nodes"])
    }
)
```

---

### Stage 4: Gap Detector

**Purpose:** Detect missing agents and trigger spawning if needed.

**Input Contract:**
```python
{
    "recommended_agents": list[str],  # From Stage 2 workflow_model
    "task_objective": str,             # Original task
}
```

**Output Contract:**
```python
{
    "gap_analysis": {
        "gaps_identified": bool,
        "missing_agents": list[str],
        "generation_required": bool,
        "generation_plan": dict,
        "can_proceed": bool
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Scan agents/ and .claude/agents/ directories |
| `on_exit` | Stage completion | Store `gap_analysis` in PipelineSnapshot.artifacts |
| `on_gap_found` | Gaps detected | **TRIGGER AGENT_SPAWN stage** |
| `on_no_gaps` | No gaps | Transition directly to PIPELINE_EXECUTOR_STAGE |
| `on_error` | Exception | Log error, transition to FAILED state |

**ComponentLoader Interactions:**
```python
# SCAN: All agent files
agent_files = []
agent_files.extend(loader.list_components("agents"))
agent_files.extend(glob(".claude/agents/*.yml"))

# PARSE: Extract capabilities from frontmatter
available_agents = []
for agent_file in agent_files:
    agent = loader.load_component(agent_file)
    available_agents.append({
        "id": agent["frontmatter"]["id"],
        "capabilities": agent["frontmatter"].get("capabilities", []),
        "source": agent_file
    })

# WRITE: Gap analysis report
if gap_analysis["gaps_identified"]:
    loader.save_component(
        "reports/gap-analysis.md",
        content=formatted_gaps,
        frontmatter={
            "template_type": "reports",
            "missing_count": len(missing_agents)
        }
    )
```

---

### Stage 5a: Agent Spawn (Conditional)

**Purpose:** Generate missing agents via Master Ecosystem Creator.

**Input Contract:**
```python
{
    "gap_analysis": dict,        # From Stage 4
    "domain_blueprint": dict,    # From Stage 1
}
```

**Output Contract:**
```python
{
    "spawn_result": {
        "generation_triggered": bool,
        "agents_spawned": list[str],
        "generation_status": str,    # pending|running|completed|failed
        "mcp_tool_call": str,
        "clear_thought_analysis": dict
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Log spawn initiation |
| `on_spawn` | Per agent spawned | Log agent creation, invalidate agent cache |
| `on_done` | All agents spawned | Reload agent registry, transition to PIPELINE_EXECUTOR_STAGE |
| `on_error` | Exception | Log error, transition to FAILED state |

**ComponentLoader Interactions:**
```python
# GENERATE: New agent definitions
for agent_id in missing_agents:
    # Load template
    template = loader.load_component("templates/agent-definition.md")

    # Render with variables
    rendered = loader.render_component(
        "templates/agent-definition.md",
        variables={
            "{{AGENT_ID}}": agent_id,
            "{{TARGET_DOMAIN}}": domain_blueprint["primary_domain"]
        }
    )

    # Save new agent
    loader.save_component(
        f"agents/{agent_id}.md",
        content=rendered,
        frontmatter={
            "template_id": agent_id,
            "template_type": "agents",
            "version": "1.0.0"
        }
    )

# INVALIDATE: Agent cache
loader.clear_cache()
```

---

### Stage 5b: Pipeline Executor

**Purpose:** Execute agent sequence defined in loom topology.

**Input Contract:**
```python
{
    "loom_topology": dict,       # From Stage 3
    "domain_blueprint": dict,    # From Stage 1
}
```

**Output Contract:**
```python
{
    "execution_result": {
        "status": str,            # success|failed|partial
        "artifacts": dict,
        "agent_outputs": dict[str, dict],
        "quality_score": float,
        "execution_log": list[dict]
    }
}
```

**Hook Triggers:**
| Hook | Trigger | Action |
|------|---------|--------|
| `on_enter` | Stage entry | Load loom topology, initialize agents |
| `on_agent_start` | Per agent execution | Log agent invocation |
| `on_agent_complete` | Per agent completion | Store agent output in artifacts |
| `on_exit` | Stage completion | Transition to COMPLETED state |
| `on_error` | Exception | Log error, transition to FAILED state |

**ComponentLoader Interactions:**
```python
# READ: Loom topology
loom = loader.load_component("documents/loom-topology.md")

# READ: Agent configurations
for agent_id in agent_sequence:
    agent_def = loader.load_component(f"agents/{agent_id}.md")
    # Configure agent from frontmatter

# WRITE: Execution results
loader.save_component(
    "results/execution-result.md",
    content=formatted_results,
    frontmatter={
        "template_type": "results",
        "status": execution_result["status"]
    }
)
```

---

## Pipeline State Transitions

### State Machine Definition

```python
VALID_TRANSITIONS = {
    "INITIALIZING": {"READY", "FAILED"},
    "READY": {"RUNNING", "CANCELLED"},
    "RUNNING": {"PAUSED", "COMPLETED", "FAILED"},

    # Stage transitions (substates of RUNNING)
    "RUNNING.DOMAIN_ANALYZER": {"RUNNING.WORKFLOW_MODELER", "FAILED"},
    "RUNNING.WORKFLOW_MODELER": {"RUNNING.LOOM_BUILDER", "FAILED"},
    "RUNNING.LOOM_BUILDER": {"RUNNING.GAP_DETECTOR", "FAILED"},
    "RUNNING.GAP_DETECTOR": {
        "RUNNING.AGENT_SPAWN",      # If gaps identified
        "RUNNING.PIPELINE_EXECUTOR", # If no gaps
        "FAILED"
    },
    "RUNNING.AGENT_SPAWN": {"RUNNING.PIPELINE_EXECUTOR", "FAILED"},
    "RUNNING.PIPELINE_EXECUTOR": {"COMPLETED", "FAILED"},

    "PAUSED": {"RUNNING", "CANCELLED"},
    "COMPLETED": set(),  # Terminal
    "FAILED": set(),     # Terminal
    "CANCELLED": set(),  # Terminal
}
```

### Transition Hook Points

```python
class PipelineHooks:
    """Hook definitions for pipeline state transitions."""

    # Global hooks
    on_state_enter = []       # Called on any state entry
    on_state_exit = []        # Called on any state exit
    on_error = []             # Called on any error

    # Stage-specific hooks
    on_domain_analysis_complete = []
    on_workflow_modeling_complete = []
    on_loom_building_complete = []
    on_gap_detection_complete = []
    on_agent_spawn_complete = []
    on_pipeline_execution_complete = []

    # Conditional hooks
    on_gaps_found = []        # Triggered by GapDetector
    on_agents_spawned = []    # Triggered after AgentSpawn
    on_quality_threshold_met = []
    on_quality_threshold_failed = []
```

---

## Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PIPELINE DATA FLOW                               │
└─────────────────────────────────────────────────────────────────────────┘

    External
       │
       ▼
┌─────────────────┐
│ task_description│
│     (input)     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        STAGE 1: DOMAIN ANALYZER                          │
│                                                                          │
│  Input: task_description                                                 │
│  ComponentLoader: READ checklists, WRITE knowledge                       │
│  Output: domain_blueprint ─────────────────────────────────┐            │
└────────────────────────────────────────────────────────────┼────────────┘
         │                                                   │
         ▼                                                   │
┌────────────────────────────────────────────────────────────┼────────────┐
│                       STAGE 2: WORKFLOW MODELER            │            │
│                                                                          │
│  Input: domain_blueprint ─────────────────────────────────┘            │
│  ComponentLoader: READ workflows, WRITE tasks                          │
│  Output: workflow_model ─────────────────────────────────┐            │
│          - recommended_agents ────────────────────────────┼───────┐    │
└────────────────────────────────────────────────────────────┼───────┼───┘
         │                                                   │       │
         ▼                                                   │       │
┌────────────────────────────────────────────────────────────┼───────┼───┐
│                        STAGE 3: LOOM BUILDER               │       │   │
│                                                                          │
│  Input: workflow_model, domain_blueprint                                 │
│  ComponentLoader: READ agents/*, WRITE documents                         │
│  Output: loom_topology ───────────────────────────────────┼───────┼───┐│
│          - agent_sequence                                                  │
│          - component_bindings                                              │
└────────────────────────────────────────────────────────────┼───────┼───┼│
         │                                                   │       │   ││
         ▼                                                   │       │   ││
┌────────────────────────────────────────────────────────────┼───────┼───┼│
│                       STAGE 4: GAP DETECTOR                │       │   ││
│                                                                          │
│  Input: recommended_agents ────────────────────────────────┘       │   ││
│          task_objective                                            │   ││
│  ComponentLoader: SCAN agents/, WRITE reports                      │   ││
│  Output: gap_analysis                                              │   ││
│          - gaps_identified? ──YES──► [STAGE 5a]                    │   ││
│                          ──NO───► [STAGE 5b]                       │   ││
└─────────────────────────────────────────┬──────────────────────────┘   ││
         │                                │                               ││
         │ YES (gaps)                     │ NO (no gaps)                  ││
         ▼                                ▼                               ││
┌────────────────────────┐    ┌───────────────────────────────────────────┘│
│    STAGE 5a: AGENT     │    │   STAGE 5b: PIPELINE EXECUTOR             │
│         SPAWN          │    │                                           │
│                        │    │  Input: loom_topology ────────────────────┘│
│  Input: gap_analysis   │    │         domain_blueprint ──────────────────┘
│         domain_blueprint│    │  ComponentLoader: READ loom, agents      │
│  ComponentLoader:      │    │  Output: execution_result                 │
│    GENERATE agents/*   │    │                                           │
│  Output: spawned list  │    └───────────────────┬───────────────────────┘
└───────────┬────────────┘                        │
            │                                     │
            └─────────────────────────────────────┘
                          │
                          ▼
                ┌─────────────────┐
                │    COMPLETED    │
                │  (final state)  │
                └─────────────────┘
```

---

## ComponentLoader API Reference

### Methods Used in Pipeline

| Method | Stage | Purpose |
|--------|-------|---------|
| `load_component(path)` | All stages | Load template/component from filesystem |
| `render_component(path, variables)` | Agent Spawn | Render template with variable substitution |
| `save_component(path, content, frontmatter)` | All stages | Write component to filesystem |
| `list_components(type)` | Gap Detector | List components by type |
| `validate_component(path)` | All stages | Validate component structure |
| `clear_cache()` | Agent Spawn | Invalidate loaded component cache |

### Component Paths by Stage

| Stage | Read Paths | Write Paths |
|-------|------------|-------------|
| Domain Analyzer | `checklists/domain-analysis.md`<br>`knowledge/*.md` | `knowledge/domain-analysis-result.md` |
| Workflow Modeler | `workflows/*.md` | `tasks/task-breakdown.md` |
| Loom Builder | `agents/*.md` | `documents/loom-topology.md` |
| Gap Detector | `agents/*.md`<br>`.claude/agents/*.yml` | `reports/gap-analysis.md` |
| Agent Spawn | `templates/agent-definition.md` | `agents/{agent_id}.md` |
| Pipeline Executor | `documents/loom-topology.md`<br>`agents/{id}.md` | `results/execution-result.md` |

---

## Error Handling

### Error Propagation

```
Stage Error ──► on_error hook ──► Log error details
                       │
                       ▼
              Set error_message in PipelineSnapshot
                       │
                       ▼
              Transition to FAILED state
                       │
                       ▼
              Return error context to caller
```

### Recovery Strategies

| Error Type | Recovery Strategy |
|------------|-------------------|
| Component not found | Retry with fallback template |
| Invalid frontmatter | Log warning, use defaults |
| Agent execution failure | Retry with backoff, then fail stage |
| Gap detection failure | Assume no gaps, proceed to execution |
| Agent spawn failure | Fail pipeline (cannot proceed without agents) |

---

## References

- Pipeline State Machine: `src/gaia/pipeline/state.py`
- Pipeline Orchestrator: `src/gaia/pipeline/orchestrator.py`
- Gap Detector: `src/gaia/pipeline/stages/gap_detector.py`
- Component Loader: `src/gaia/utils/component_loader.py`
- ADR-001: Python Classes vs MD-Format for Phase 5 Pipeline Agents
