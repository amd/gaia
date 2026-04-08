---
template_id: pipeline-workflow
template_type: workflows
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: GAIA multi-stage pipeline workflow for agent ecosystem generation
schema_version: "1.0"
---

# Pipeline Workflow Pattern

## Purpose

This workflow pattern defines GAIA's multi-stage agent ecosystem generation pipeline. It coordinates domain analysis, workflow modeling, loom building, and ecosystem construction across specialized pipeline stages.

## Workflow Characteristics

- **Flow Direction:** Multi-stage with artifact handoffs
- **Decision Points:** Stage gates with quality validation
- **Documentation:** Structured artifacts at each stage
- **Change Management:** Iteration tracking and regeneration

## Pipeline Stage Structure

```
+------------------+
| Stage 1:         |
| Domain Analyzer  |
|                  |
| Input: Task      |
| Output: Blueprint|
+--------+---------+
         |
         v
+--------+---------+
| Stage 2:         |
| Workflow Modeler |
|                  |
| Input: Blueprint |
| Output: Graph    |
+--------+---------+
         |
         v
+--------+---------+
| Stage 3:         |
| Loom Builder     |
|                  |
| Input: Graph     |
| Output: Topology |
+--------+---------+
         |
         v
+--------+---------+
| Stage 4:         |
| Ecosystem Builder|
|                  |
| Input: Gap List  |
| Output: Agents   |
+------------------+
```

## Stage Definitions

### Stage 1: Domain Analyzer

**Purpose:** Analyze input task to identify domains, requirements, and agent needs.

**Agent:** `domain-analyzer`

**Input Contract:**
- `task`: Natural language task description
- `context`: Additional context and constraints
- `user_info`: User preferences and history

**Processing:**
1. Extract keywords and domain signals
2. Match against domain taxonomy
3. Identify domain requirements
4. Map cross-domain dependencies
5. Generate agent taxonomy

**Output Contract:**
- `blueprint`: Structured analysis document
- `domain_registry`: Identified domains with attributes
- `agent_taxonomy`: Required agents with capabilities
- `ecosystem_handoff`: Machine-parseable agent stubs

**Quality Gates:**
- [ ] Primary domain clearly identified
- [ ] All relevant secondary domains listed
- [ ] Requirements are specific and actionable
- [ ] Dependencies accurately mapped
- [ ] Agent stubs include interface contracts

**Artifact:** `blueprints/{{TASK_SLUG}}_blueprint.md`

### Stage 2: Workflow Modeler

**Purpose:** Design execution workflow based on domain analysis.

**Agent:** `workflow-modeler`

**Input Contract:**
- `blueprint`: Stage 1 analysis output
- `workflow_templates`: Available workflow patterns

**Processing:**
1. Select appropriate workflow pattern
2. Design stage sequence
3. Define data flow between stages
4. Identify decision gates
5. Specify shared context requirements

**Output Contract:**
- `workflow_model`: Execution graph specification
- `stage_registry`: Stage definitions with dependencies
- `data_flow`: Edge specifications
- `decision_gates`: Gate conditions and paths

**Quality Gates:**
- [ ] Workflow pattern matches task characteristics
- [ ] All stages have clear entry/exit criteria
- [ ] Data flow is complete and consistent
- [ ] Decision gates cover all branches
- [ ] Resource requirements specified

**Artifact:** `workflows/{{TASK_SLUG}}_workflow.md`

### Stage 3: Loom Builder

**Purpose:** Generate pipeline topology and identify agent gaps.

**Agent:** `loom-builder`

**Input Contract:**
- `workflow_model`: Stage 2 execution graph
- `agent_registry`: Available agents
- `pipeline_templates`: Pipeline configuration templates

**Processing:**
1. Map workflow stages to pipeline configuration
2. Check agent availability for each stage
3. Generate loom topology YAML
4. Identify missing agents (gap list)
5. Define error handling strategy

**Output Contract:**
- `loom_topology`: Pipeline configuration YAML
- `gap_list`: Missing agent specifications
- `error_handling`: Retry and fallback policies

**Quality Gates:**
- [ ] Pipeline configuration is valid YAML
- [ ] All stages have assigned agents or gap entries
- [ ] Dependencies correctly specified
- [ ] Error handling covers expected failures
- [ ] Resource budgets estimated

**Artifacts:**
- `pipelines/{{TASK_SLUG}}_pipeline.yaml`
- `gaps/{{TASK_SLUG}}_gap_list.md`

### Stage 4: Ecosystem Builder

**Purpose:** Generate missing agents from gap list.

**Agent:** `ecosystem-builder`

**Input Contract:**
- `gap_list`: Missing agent specifications
- `template_library`: Agent definition templates
- `component_templates`: Component scaffolding

**Processing:**
1. Parse gap list for agent stubs
2. Select appropriate templates
3. Populate templates with stub data
4. Generate agent files
5. Validate generated agents load correctly

**Output Contract:**
- `agent_files`: Generated agent .md files
- `component_files`: Supporting components
- `ecosystem_manifest`: Summary of generated ecosystem

**Quality Gates:**
- [ ] All gap list agents generated
- [ ] Generated agents load in registry
- [ ] Frontmatter is valid YAML
- [ ] System prompts are non-empty
- [ ] Tool-call syntax conforms to spec

**Artifacts:**
- `config/agents/{{AGENT_ID}}.md` (per agent)
- `ecosystems/{{TASK_SLUG}}_manifest.md`

## Pipeline Execution Flow

```
Pipeline Start
     |
     v
+----+----+
| Stage 1 |-----> Quality Check -----> Fail: Regenerate or Escalate
+----+----+       (Pass/Fail)         Pass: Continue
     |
     v
+----+----+
| Stage 2 |-----> Quality Check -----> Fail: Regenerate or Escalate
+----+----+       (Pass/Fall)         Pass: Continue
     |
     v
+----+----+
| Stage 3 |-----> Quality Check -----> Fail: Regenerate or Escalate
+----+----+       (Pass/Fail)         Pass: Continue
     |
     v
+----+----+
| Stage 4 |-----> Quality Check -----> Fail: Regenerate or Escalate
+----+----+       (Pass/Fail)         Pass: Complete
     |
     v
Pipeline Complete
```

## Tool Invocation Patterns

### Pipeline Execution

```python
# Start pipeline execution
pipeline_run = await self.tools.pipeline.start(
    task="{{TASK_DESCRIPTION}}",
    context={{CONTEXT}},
    pipeline_id="{{PIPELINE_ID}}"
)

# Execute specific stage
stage_result = await self.tools.pipeline.execute_stage(
    stage_id="{{STAGE_ID}}",
    agent_id="{{AGENT_ID}}",
    input_artifacts={{INPUT_ARTIFACTS}},
    quality_gate=True
)

# Handle stage failure
if not stage_result.passed:
    action = await self.tools.pipeline.handle_failure(
        stage_id="{{STAGE_ID}}",
        failure_reason="{{FAILURE_REASON}}",
        options=["RETRY", "REGENERATE", "ESCALATE"]
    )
```

### Artifact Management

```python
# Save stage artifact
artifact = await self.tools.pipeline.save_artifact(
    stage_id="{{STAGE_ID}}",
    artifact_type="{{ARTIFACT_TYPE}}",
    content={{CONTENT}},
    format="{{FORMAT}}"
)

# Load artifact for next stage
input_artifact = await self.tools.pipeline.load_artifact(
    stage_id="{{NEXT_STAGE_ID}}",
    artifact_type="{{INPUT_TYPE}}",
    from_stage="{{FROM_STAGE}}"
)

# Validate artifact
validation = await self.tools.pipeline.validate_artifact(
    artifact_id="{{ARTIFACT_ID}}",
    schema="{{ARTIFACT_SCHEMA}}",
    quality_criteria={{CRITERIA}}
)
```

### Handoff Protocol

```python
# Record stage handoff
handoff = await self.tools.pipeline.record_handoff(
    from_stage="{{FROM_STAGE}}",
    to_stage="{{TO_STAGE}}",
    artifacts={{ARTIFACT_LIST}},
    handoff_status="{{STATUS}}"
)

# Verify handoff received
confirmation = await self.tools.pipeline.confirm_handoff(
    stage_id="{{TO_STAGE}}",
    expected_artifacts={{EXPECTED_LIST}},
    received_artifacts={{RECEIVED_LIST}}
)
```

## Quality Gate Specification

### Quality Check Process

```
Stage Output
     |
     v
+-----------+
| Validate  |-----> Invalid: Report Error
| Structure |
+-----------+
     |
   Valid
     |
     v
+-----------+
| Check     |-----> Incomplete: Request Completion
| Completeness|
+-----------+
     |
   Complete
     |
     v
+-----------+
| Assess    |-----> Below Threshold: Improve or Escalate
| Quality   |
+-----------+
     |
   Pass
     |
     v
Continue to Next Stage
```

### Quality Metrics

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| Structure Validity | 100% | YAML/schema validation |
| Completeness | 100% | Required fields present |
| Quality Score | > 0.7 | Agent assessment |
| Load Success | 100% | Registry load test |

## When to Use

**Pipeline workflow is appropriate when:**
- Complex tasks requiring multi-stage analysis
- Agent ecosystem generation needed
- Structured artifact production required
- Quality gates are valued
- Reproducible process is important

**Pipeline workflow is NOT appropriate when:**
- Simple tasks needing direct execution
- Rapid prototyping without documentation
- Single-agent tasks
- Human-in-the-loop at each stage not needed

## Quality Criteria

- [ ] Each stage receives complete input artifacts
- [ ] Stage outputs meet quality thresholds
- [ ] Artifacts are persisted and traceable
- [ ] Failures are handled appropriately
- [ ] Pipeline completion produces loadable agents

## Related Components

- [[component-framework/personas/pipeline-agent.md]] - Pipeline orchestration persona
- [[component-framework/workflows/waterfall-workflow.md]] - Sequential workflow reference
- [[component-framework/documents/status-report.md]] - Pipeline status reporting
