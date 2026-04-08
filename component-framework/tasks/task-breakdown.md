---
template_id: task-breakdown
template_type: tasks
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Task breakdown template for hierarchical task decomposition
schema_version: "1.0"
---

# Task Breakdown: {{TASK_NAME}}

## Purpose

This template decomposes a complex task into manageable subtasks with clear dependencies, effort estimates, and work packages.

## Task Overview

**Task Name:** {{TASK_NAME}}

**Task ID:** {{TASK_ID}}

**Parent Task:** [Link to parent task if applicable]

**Description:**
[What this task aims to accomplish]

**Priority:** P0 | P1 | P2 | P3

**Status:** Planning | In Progress | Blocked | Complete

## Subtasks

| ID | Name | Description | Complexity | Estimated Effort | Dependencies | Owner | Status |
|----|------|-------------|------------|------------------|--------------|-------|--------|
| 1 | {{NAME}} | {{DESC}} | {{SCORE}} | {{EFFORT}} | {{DEPS}} | {{OWNER}} | {{STATUS}} |
| 2 | {{NAME}} | {{DESC}} | {{SCORE}} | {{EFFORT}} | {{DEPS}} | {{OWNER}} | {{STATUS}} |

### Subtask Details

#### Subtask {{ID}}: {{SUBTASK_NAME}}

**Description:**
[Detailed description of this subtask]

**Acceptance Criteria:**
- [ ] [Criterion 1]
- [ ] [Criterion 2]

**Estimated Effort:** {{EFFORT}} (hours/story points)

**Complexity Score:** 1-5 (1=Simple, 5=Complex)

**Dependencies:**
- [Subtask IDs this depends on]

**Assigned To:** {{ASSIGNEE}}

**Status:** Not Started | In Progress | Blocked | Complete

**Completion Date:** {{DATE}}

## Work Packages

[Groupings of related subtasks]

### Work Package: {{PACKAGE_NAME}}

**Description:**
[What this work package delivers]

**Included Subtasks:**
- [Subtask ID 1]
- [Subtask ID 2]

**Package Owner:** {{OWNER}}

**Estimated Duration:** {{DURATION}}

**Status:** Not Started | In Progress | Complete

## Critical Path

[Sequence of dependent tasks determining minimum timeline]

```
{{TASK_A}} --> {{TASK_B}} --> {{TASK_C}} --> {{TASK_D}}
     |                                          ^
     +-------------> {{TASK_E}} ----------------+
```

### Critical Path Analysis

| Path | Tasks | Total Duration | Critical |
|------|-------|----------------|----------|
| Path 1 | A -> B -> C | {{DURATION}} | Yes |
| Path 2 | A -> E -> D | {{DURATION}} | No |

## Milestones

[Key checkpoints in task completion]

| Milestone | Description | Target Date | Status |
|-----------|-------------|-------------|--------|
| {{MILESTONE}} | {{DESC}} | {{DATE}} | {{STATUS}} |

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| {{RISK}} | {{PROB}} | {{IMPACT}} | {{MITIGATION}} |

## Resource Requirements

| Resource | Type | Quantity | Availability |
|----------|------|----------|--------------|
| {{RESOURCE}} | {{TYPE}} | {{QTY}} | {{AVAIL}} |

## Related Components

- [[component-framework/tasks/task-dependency.md]] - For dependency mapping
- [[component-framework/tasks/task-priority.md]] - For prioritization
- [[component-framework/tasks/task-tracking.md]] - For progress tracking
