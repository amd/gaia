---
template_id: task-priority
template_type: tasks
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Task priority template for ranking and prioritization framework
schema_version: "1.0"
---

# Task Priority Matrix

## Purpose

This template provides a framework for prioritizing tasks based on urgency, impact, effort, and other factors. It helps teams focus on the most valuable work first.

## Priority Framework

**Project/Context:** {{PROJECT_NAME}}

**Priority Date:** {{DATE}}

**Prioritized By:** {{AGENT_ID}}

**Review Cycle:** {{CYCLE}}

## Priority Scoring Model

### Scoring Factors

| Factor | Weight | Description | Scale |
|--------|--------|-------------|-------|
| Urgency | 30% | How time-sensitive is this task? | 1-5 |
| Impact | 40% | What is the business/task value? | 1-5 |
| Effort | -30% | How much work is required? (inverse) | 1-5 |

### Priority Score Formula

```
Priority Score = (Urgency × 0.3) + (Impact × 0.4) + ((6 - Effort) × 0.3)
```

## Priority Scores

| Task ID | Task Name | Urgency (1-5) | Impact (1-5) | Effort (1-5) | Priority Score | Tier |
|---------|-----------|---------------|--------------|--------------|----------------|------|
| {{ID}} | {{NAME}} | {{U}} | {{I}} | {{E}} | {{SCORE}} | {{TIER}} |

### Task Details

#### Task: {{TASK_ID}}

**Task Name:** {{TASK_NAME}}

**Scores:**
- **Urgency:** {{SCORE}}/5 - [Rationale]
- **Impact:** {{SCORE}}/5 - [Rationale]
- **Effort:** {{SCORE}}/5 - [Rationale]

**Calculated Priority Score:** {{SCORE}}

**Priority Tier:** P0 | P1 | P2 | P3

**Rationale:**
[Why this task has this priority]

## Priority Tiers

| Tier | Name | Score Range | Tasks | Rationale |
|------|------|-------------|-------|-----------|
| P0 | Critical | 4.5-5.0 | [List] | Must complete first; blockers for other work |
| P1 | High | 3.5-4.4 | [List] | Should complete early; high value |
| P2 | Medium | 2.5-3.4 | [List] | Can defer if needed; moderate value |
| P3 | Low | 1.0-2.4 | [List] | Nice to have; low urgency/impact |

## Priority Matrix (Eisenhower Box)

```
                    | Urgent      | Not Urgent  |
--------------------+-------------+-------------+
  Important         | DO FIRST    | SCHEDULE    |
                    | (P0/P1)     | (P1/P2)     |
--------------------+-------------+-------------+
  Not Important     | DELEGATE    | ELIMINATE   |
                    | (P2)        | (P3)        |
--------------------+-------------+-------------+
```

### Quadrant Analysis

#### Quadrant 1: Do First (Important + Urgent)

| Task | Reason | Deadline |
|------|--------|----------|
| {{TASK}} | {{REASON}} | {{DATE}} |

#### Quadrant 2: Schedule (Important + Not Urgent)

| Task | Reason | Target Window |
|------|--------|---------------|
| {{TASK}} | {{REASON}} | {{WINDOW}} |

#### Quadrant 3: Delegate (Not Important + Urgent)

| Task | Reason | Delegate To |
|------|--------|-------------|
| {{TASK}} | {{REASON}} | {{ASSIGNEE}} |

#### Quadrant 4: Eliminate (Not Important + Not Urgent)

| Task | Reason to Eliminate |
|------|---------------------|
| {{TASK}} | {{REASON}} |

## Priority Changes

[History of priority adjustments]

| Date | Task ID | Old Priority | New Priority | Reason | Changed By |
|------|---------|--------------|--------------|--------|------------|
| {{DATE}} | {{ID}} | {{OLD}} | {{NEW}} | {{REASON}} | {{WHO}} |

## Priority Conflicts

[Conflicts between task priorities and dependencies]

| Conflict | Tasks Involved | Resolution | Status |
|----------|---------------|------------|--------|
| {{CONFLICT}} | {{TASKS}} | {{RESOLUTION}} | {{STATUS}} |

## Related Components

- [[component-framework/tasks/task-breakdown.md]] - For task decomposition
- [[component-framework/tasks/task-dependency.md]] - For dependency considerations
- [[component-framework/tasks/task-tracking.md]] - For tracking progress by priority
