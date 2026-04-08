---
template_id: workflow-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating workflow definition files
schema_version: "1.0"
---

# Workflow Meta-Template

## Purpose

This meta-template provides the structure for generating workflow definition files. Workflows define structured execution patterns for multi-phase processes, including phase definitions, transition conditions, and quality gates.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{WORKFLOW_ID}} | Unique workflow identifier | Yes | `agile-workflow` |
| {{WORKFLOW_NAME}} | Human-readable workflow name | Yes | `Agile Workflow` |
| {{VERSION}} | Workflow version (semver) | Yes | `1.0.0` |
| {{WORKFLOW_TYPE}} | Workflow pattern type | Yes | `agile` |
| {{DESCRIPTION}} | Workflow purpose | Yes | `Iterative development` |
| {{PHASE_COUNT}} | Number of phases | Yes | `4` |
| {{PHASE_DEFINITIONS}} | Phase details | Yes | See body template |
| {{TRANSITION_RULES}} | Phase transition rules | Yes | See body template |
| {{QUALITY_GATES}} | Quality validation rules | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{WORKFLOW_ID}}
template_type: workflows
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
workflow_type: {{WORKFLOW_TYPE}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Workflow Body Template

```markdown
# {{WORKFLOW_NAME}}

## Purpose

[Describe what this workflow accomplishes and when it should be used. Explain the overall approach and benefits.]

## Workflow Identity

| Attribute | Value |
|-----------|-------|
| Workflow ID | {{WORKFLOW_ID}} |
| Type | {{WORKFLOW_TYPE}} |
| Phases | {{PHASE_COUNT}} |
| Pattern | [sequential/iterative/parallel/adaptive] |

## Applicable Scenarios

This workflow is appropriate when:
- [ ] Scenario 1
- [ ] Scenario 2
- [ ] Scenario 3

## Phase Definitions

{{PHASE_DEFINITIONS}}

### Phase 1: {{PHASE_1_NAME}}

**Purpose:** [Why this phase exists]

**Entry Criteria:**
- [ ] Precondition 1
- [ ] Precondition 2

**Activities:**
1. Activity 1
2. Activity 2
3. Activity 3

**Outputs:**
- `output_1`: Description
- `output_2`: Description

**Exit Criteria:**
- [ ] Deliverable 1 complete
- [ ] Deliverable 2 validated
- [ ] Quality gate passed

**Recommended Agents:**
- [[component-framework/personas/{{AGENT_1}}]] - Role description
- [[component-framework/personas/{{AGENT_2}}]] - Role description

### Phase 2: {{PHASE_2_NAME}}

[Continue for all phases]

## Transition Rules

{{TRANSITION_RULES}}

### Forward Transitions

| From Phase | To Phase | Condition | Action |
|------------|----------|-----------|--------|
| Phase 1 | Phase 2 | Exit criteria met | Proceed |
| Phase 2 | Phase 3 | Exit criteria met | Proceed |

### Backward Transitions

| From Phase | To Phase | Condition | Action |
|------------|----------|-----------|--------|
| Phase 3 | Phase 2 | Quality gate failed | Rework |
| Phase 2 | Phase 1 | Requirements changed | Restart |

### Skip Transitions

| From Phase | To Phase | Condition | Action |
|------------|----------|-----------|--------|
| Phase 1 | Phase 3 | Simple task detected | Skip phase 2 |

## Quality Gates

{{QUALITY_GATES}}

### Gate 1: {{GATE_1_NAME}}

**Location:** After Phase {{N}}

**Required Checks:**
| ID | Check | Pass Criteria |
|----|-------|---------------|
| R1 | Check name | Criteria |
| R2 | Check name | Criteria |

**Pass Condition:** All required checks must pass.

**On Failure:** [Describe remediation action]

### Gate 2: {{GATE_2_NAME}}

[Continue for all quality gates]

## Milestones

| Milestone | Phase | Deliverables | Success Criteria |
|-----------|-------|--------------|------------------|
| Milestone 1 | Phase 1 | Deliverable list | Criteria |
| Milestone 2 | Phase 2 | Deliverable list | Criteria |

## Component Framework Integration

### Components Created

During this workflow, the following components are typically created:
- `tasks/{{TASK_NAME}}.md` - Task breakdown
- `documents/{{DOCUMENT_NAME}}.md` - Documentation

### Components Updated

The following components may be updated:
- `memory/working-memory.md` - Progress updates
- `knowledge/{{KNOWLEDGE_NAME}}.md` - Lessons learned

## Metrics and Monitoring

### Progress Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Phase completion rate | 100% | Phases completed / Total phases |
| Quality gate pass rate | >= 90% | Gates passed / Total gates |
| Rework frequency | < 20% | Backward transitions / Total transitions |

### Health Indicators

**Healthy:** All phases progressing, quality gates passing
**At Risk:** Multiple rework cycles, quality gate failures
**Blocked:** Unable to meet exit criteria

## Error Handling

### Common Failure Modes

| Failure Mode | Detection | Recovery |
|--------------|-----------|----------|
| Phase stalled | No progress for N iterations | Escalate, reassess scope |
| Quality gate fail | Check failure analysis | Remediation plan |
| Scope creep | Requirements expanding | Freeze scope, create follow-up |

### Escalation Path

1. **Level 1:** Agent self-correction
2. **Level 2:** Coordinator agent intervention
3. **Level 3:** Human review required

## Related Workflows

- [[component-framework/workflows/{{RELATED_WORKFLOW}}]] - Related workflow

## Usage Examples

### Example 1: Standard Execution

```
Workflow: {{WORKFLOW_ID}}
Phase 1: Complete {{PHASE_1_NAME}}
  - Activities: [list completed]
  - Outputs: [list produced]
Phase 2: Begin {{PHASE_2_NAME}}
  - Transition: Approved (exit criteria met)
```

## Quality Checklist

- [ ] All phases have clear entry/exit criteria
- [ ] Transition rules cover forward, backward, and skip scenarios
- [ ] Quality gates have specific, measurable criteria
- [ ] Milestones are defined with clear deliverables
- [ ] Component integration is documented
- [ ] Error handling covers common failure modes
- [ ] Metrics are measurable and meaningful

## References

- [[component-framework/templates/agent-definition.md]] - Agent definitions
- [[component-framework/checklists/{{RELATED_CHECKLIST}}]] - Quality checklists
```

## Generation Instructions

### Step 1: Define Workflow Purpose

Articulate:
1. What problem this workflow solves
2. When to use this workflow vs others
3. What type of pattern (sequential, iterative, etc.)

### Step 2: Specify Phases

For each phase:
- Define clear purpose
- List entry criteria (must be satisfied before starting)
- Document activities
- Specify outputs
- Define exit criteria (must be satisfied to proceed)

### Step 3: Define Transitions

Specify:
- Normal forward transitions
- Backward transitions for rework
- Skip transitions for efficiency

### Step 4: Establish Quality Gates

Define:
- Gate locations (typically after key phases)
- Required checks with pass criteria
- Failure handling procedures

### Step 5: Validate Generated Workflow

```python
# Load and validate the generated workflow
loader = ComponentLoader()
workflow = loader.load_component(f"workflows/{{WORKFLOW_ID}}.md")
errors = loader.validate_component(f"workflows/{{WORKFLOW_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify workflow structure
assert workflow['frontmatter']['workflow_type'] == '{{WORKFLOW_TYPE}}'
assert len(workflow['content']) > 0
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `workflows`
- [ ] `workflow_type` matches a valid pattern
- [ ] All phases have complete definitions
- [ ] Transition rules are comprehensive
- [ ] Quality gates have measurable criteria
- [ ] Component integration is documented
- [ ] Related workflows are correctly linked

## Related Components

- [[component-framework/templates/agent-definition.md]] - Agent definition template
- [[component-framework/templates/checklist-template.md]] - Checklist generation template
- [[component-framework/workflows/pipeline-workflow.md]] - Pipeline workflow example
- [[component-framework/workflows/agile-workflow.md]] - Agile workflow example
