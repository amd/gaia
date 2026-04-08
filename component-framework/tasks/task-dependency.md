---
template_id: task-dependency
template_type: tasks
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Task dependency template for prerequisite mapping
schema_version: "1.0"
---

# Task Dependencies

## Purpose

This template maps dependencies between tasks, identifying prerequisites, successors, and critical dependency chains that affect project timelines.

## Dependency Overview

**Project/Task Set:** {{PROJECT_NAME}}

**Analysis Date:** {{DATE}}

**Total Tasks:** {{COUNT}}

**Total Dependencies:** {{COUNT}}

**Critical Path Length:** {{DURATION}}

## Dependency Graph

```
{{GRAPH_VISUALIZATION}}

Example:
Task A --> Task B --> Task C
  |          ^
  v          |
Task D ------+
```

## Dependency Types

| Type | Code | Description | Example |
|------|------|-------------|---------|
| Finish-to-Start | FS | B cannot start until A finishes | Code review after implementation |
| Start-to-Start | SS | B cannot start until A starts | Testing starts when coding starts |
| Finish-to-Finish | FF | B cannot finish until A finishes | Documentation finishes with code |
| Start-to-Finish | SF | B cannot finish until A starts | Rare, specialized cases |

## Task Dependencies

| Task ID | Task Name | Predecessors | Successors | Dependency Type | Lag |
|---------|-----------|--------------|------------|-----------------|-----|
| {{ID}} | {{NAME}} | {{PREDS}} | {{SUCCS}} | {{TYPE}} | {{LAG}} |

### Dependency Details

#### Dependency: {{DEP_ID}}

**Source Task:**
- **ID:** {{SOURCE_ID}}
- **Name:** {{SOURCE_NAME}}

**Target Task:**
- **ID:** {{TARGET_ID}}
- **Name:** {{TARGET_NAME}}

**Dependency Type:** FS | SS | FF | SF

**Lag Time:** {{LAG}} (positive = delay, negative = overlap)

**Description:**
[Why this dependency exists]

**Critical:** Yes | No

**Risk Level:** High | Medium | Low

## Critical Path Analysis

### Critical Path Tasks

| Sequence | Task ID | Task Name | Duration | Early Start | Late Start | Slack |
|----------|---------|-----------|----------|-------------|------------|-------|
| 1 | {{ID}} | {{NAME}} | {{DUR}} | {{ES}} | {{LS}} | {{SLACK}} |

### Path Calculation

```
Critical Path: {{TASK_A}} -> {{TASK_B}} -> {{TASK_C}}
Total Duration: {{TOTAL_DURATION}}
```

## Dependency Risks

| Risk ID | Description | Affected Tasks | Probability | Impact | Mitigation |
|---------|-------------|----------------|-------------|--------|------------|
| {{ID}} | {{DESC}} | {{TASKS}} | {{PROB}} | {{IMPACT}} | {{MIT}} |

## External Dependencies

[Dependencies on external systems, teams, or resources]

| Dependency | Owner | Status | Expected Date | Impact if Delayed |
|------------|-------|--------|---------------|-------------------|
| {{DEP}} | {{OWNER}} | {{STATUS}} | {{DATE}} | {{IMPACT}} |

## Resource Dependencies

[Dependencies based on resource availability]

| Resource | Tasks Requiring | Availability | Conflict Resolution |
|----------|-----------------|--------------|---------------------|
| {{RESOURCE}} | {{TASKS}} | {{AVAIL}} | {{RESOLUTION}} |

## Dependency Matrix

[Grid showing task interdependencies]

```
       | T1 | T2 | T3 | T4 | T5 |
-------+----+----+----+----+----+
 T1    |  - | FS |  - | SS |  - |
 T2    |  - |  - | FF |  - |  - |
 T3    |  - |  - |  - | FS |  - |
 T4    |  - |  - |  - |  - | FS |
 T5    |  - |  - |  - |  - |  - |

Legend: FS = Finish-to-Start, SS = Start-to-Start, FF = Finish-to-Finish
```

## Related Components

- [[component-framework/tasks/task-breakdown.md]] - For task decomposition
- [[component-framework/tasks/task-priority.md]] - For priority considerations
- [[component-framework/tasks/task-tracking.md]] - For tracking dependent task progress
