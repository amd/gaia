---
template_id: task-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating task definition files
schema_version: "1.0"
---

# Task Meta-Template

## Purpose

This meta-template provides the structure for generating task definition files. Tasks define multi-step workflows that agents execute, including task breakdowns, dependencies, priorities, and tracking.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{TASK_ID}} | Unique task identifier | Yes | `task-breakdown` |
| {{TASK_NAME}} | Human-readable task name | Yes | `Task Breakdown` |
| {{VERSION}} | Task version (semver) | Yes | `1.0.0` |
| {{TASK_TYPE}} | Type of task definition | Yes | `breakdown`, `tracking`, `dependency` |
| {{DESCRIPTION}} | Task purpose | Yes | `Breaks down complex tasks` |
| {{OWNER_AGENT}} | Owning/primary agent | No | `domain-analyzer` |
| {{SUBTASK_COUNT}} | Number of subtasks | No | `5` |
| {{STEP_DEFINITIONS}} | Step details | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{TASK_ID}}
template_type: tasks
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
task_type: {{TASK_TYPE}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Task Body Template

```markdown
# {{TASK_NAME}}

## Purpose

[Describe what this task workflow accomplishes. Explain the goal and when this task definition should be used.]

## Task Identity

| Attribute | Value |
|-----------|-------|
| Task ID | {{TASK_ID}} |
| Type | {{TASK_TYPE}} |
| Owner Agent | {{OWNER_AGENT}} |
| Subtasks | {{SUBTASK_COUNT}} |

## Input Requirements

| Input | Type | Description | Source |
|-------|------|-------------|--------|
| `task_description` | string | High-level task | User input |
| `context` | dict | Additional context | Working memory |

## Workflow Steps

{{STEP_DEFINITIONS}}

### Step 1: {{STEP_1_NAME}}

**Purpose:** [Why this step exists]

**Activities:**
1. Activity 1
2. Activity 2

**Tool Calls:**
```tool-call
CALL: {{TOOL_NAME}} "{{PARAMETERS}}"
purpose: {{TOOL_PURPOSE}}
capture: {{CAPTURE_VARIABLE}}
```

**Success Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2

**Output:**
- `{{OUTPUT_NAME}}`: Description

### Step 2: {{STEP_2_NAME}}

[Continue for all steps]

## Task Breakdown

### Subtask 1: {{SUBTASK_1_NAME}}

**Description:** [What this subtask accomplishes]

**Dependencies:**
- [ ] Prerequisite task 1
- [ ] Prerequisite task 2

**Estimated Effort:** [Time/complexity estimate]

**Assigned Agent:** [[component-framework/personas/{{ASSIGNED_AGENT}}]]

### Subtask 2: {{SUBTASK_2_NAME}}

[Continue for all subtasks]

## Dependency Map

```
{{TASK_DEPENDENCY_GRAPH}}
```

### Dependency Table

| Task | Depends On | Blocked By | Blocks |
|------|------------|------------|--------|
| Task 1 | None | None | Task 2, Task 3 |
| Task 2 | Task 1 | Task 1 | Task 4 |

## Priority Assignment

### Priority Matrix

| Priority | Task | Urgency | Impact | Effort |
|----------|------|---------|--------|--------|
| P1 | Task 1 | High | High | Medium |
| P2 | Task 2 | Medium | High | High |

### Priority Rationale

[Explain why tasks are prioritized in this order]

## Output Specification

| Output | Type | Description | Consumers |
|--------|------|-------------|-----------|
| `task_plan` | dict | Structured task plan | Downstream agents |
| `subtasks` | list | List of subtasks | Task executor |

## Failure Handling

### Common Failure Modes

| Failure Mode | Detection | Recovery |
|--------------|-----------|----------|
| Task blocked | Dependency not met | Unblock dependency |
| Timeout | No progress for N min | Escalate or reroute |
| Invalid input | Input validation fail | Request clarification |

### Retry Logic

- **Max Retries:** 3
- **Backoff Strategy:** Exponential (1x, 2x, 4x)
- **On Exhaustion:** Escalate to coordinator

## Component Framework Integration

### Components Created

This task may create:
- `documents/{{DOCUMENT_NAME}}.md` - Task documentation
- `tasks/{{SUBTASK_NAME}}.md` - Subtask definitions

### Components Updated

This task may update:
- `memory/working-memory.md` - Progress tracking
- `tasks/task-tracking.md` - Status updates

## Metrics and Monitoring

### Progress Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Completion rate | 100% | Completed / Total tasks |
| On-time delivery | >= 90% | On-time / Total tasks |
| Blocker resolution | < 1 hour | Average resolution time |

### Status States

| State | Description | Next Action |
|-------|-------------|-------------|
| `pending` | Not yet started | Wait for dependencies |
| `in_progress` | Currently executing | Continue execution |
| `blocked` | Cannot proceed | Resolve blocker |
| `completed` | Successfully finished | Proceed to next task |

## Examples

### Example 1: Simple Task

```
Task: {{SIMPLE_TASK_NAME}}
Subtasks: 3
Estimated Effort: 2 hours
```

### Example 2: Complex Task

```
Task: {{COMPLEX_TASK_NAME}}
Subtasks: 8
Dependencies: [Task A, Task B]
Estimated Effort: 2 weeks
```

## Related Tasks

- [[component-framework/tasks/{{RELATED_TASK}}]] - Related task

## Quality Checklist

- [ ] All steps have clear purpose and activities
- [ ] Success criteria are specific and measurable
- [ ] Dependencies are correctly mapped
- [ ] Priorities are justified
- [ ] Failure handling is comprehensive
- [ ] Component integration is documented

## References

- [[component-framework/templates/workflow-template.md]] - Workflow template
- [[component-framework/checklists/{{RELATED_CHECKLIST}}]] - Quality checklist
```

## Generation Instructions

### Step 1: Define Task Purpose

Articulate:
1. What goal the task achieves
2. When this task should be created
3. What type of task (breakdown, tracking, dependency)

### Step 2: Specify Workflow Steps

For each step:
- Define clear purpose
- List specific activities
- Include tool calls if applicable
- Specify success criteria

### Step 3: Break Down Subtasks

Identify:
- All subtasks required
- Dependencies between subtasks
- Estimated effort per subtask
- Agent assignments

### Step 4: Map Dependencies

Create:
- Dependency graph/flow
- Dependency table
- Blocker identification

### Step 5: Assign Priorities

Consider:
- Urgency (time sensitivity)
- Impact (value delivered)
- Effort (resources required)
- Dependencies (critical path)

### Step 6: Validate Generated Task

```python
# Load and validate the generated task
loader = ComponentLoader()
task = loader.load_component(f"tasks/{{TASK_ID}}.md")
errors = loader.validate_component(f"tasks/{{TASK_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify task structure
assert task['frontmatter']['task_type'] == '{{TASK_TYPE}}'
assert task['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `tasks`
- [ ] `task_type` is valid
- [ ] Workflow steps are complete
- [ ] Subtasks are well-defined
- [ ] Dependencies are accurately mapped
- [ ] Priorities are justified
- [ ] Failure handling is comprehensive

## Related Components

- [[component-framework/templates/workflow-template.md]] - Workflow generation template
- [[component-framework/templates/checklist-template.md]] - Checklist template
- [[component-framework/tasks/task-breakdown.md]] - Task breakdown example
- [[component-framework/tasks/task-dependency.md]] - Dependency mapping example
