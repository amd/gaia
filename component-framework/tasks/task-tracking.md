---
template_id: task-tracking
template_type: tasks
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Task tracking template for progress tracking and status management
schema_version: "1.0"
---

# Task Tracking: {{TASK_NAME}}

## Purpose

This template tracks the progress, status, and completion of tasks over time. It provides visibility into task state, blockers, and completion criteria.

## Task Overview

**Task Name:** {{TASK_NAME}}

**Task ID:** {{TASK_ID}}

**Description:**
[Brief description of the task]

## Current Status

- **Status:** {{STATUS}} (PLANNING | IN_PROGRESS | BLOCKED | ON_HOLD | COMPLETE)
- **Started:** {{START_DATE}}
- **Due Date:** {{DUE_DATE}}
- **Owner:** {{OWNER}}
- **Contributors:** {{LIST}}
- **Last Updated:** {{TIMESTAMP}}

### Status History

| Date | Status | Changed By | Reason |
|------|--------|------------|--------|
| {{DATE}} | {{STATUS}} | {{WHO}} | {{REASON}} |

## Progress Log

| Entry ID | Date | Agent | Action | Outcome | Next Step | Time Spent |
|----------|------|-------|--------|---------|-----------|------------|
| {{ID}} | {{DATE}} | {{AGENT}} | {{ACTION}} | {{OUTCOME}} | {{NEXT}} | {{TIME}} |

### Progress Entries

#### Entry {{ID}}

**Timestamp:** {{TIMESTAMP}}

**Agent:** {{AGENT_ID}}

**Action Taken:**
[What was done]

**Outcome:**
[What resulted from the action]

**Evidence:**
[Links to artifacts, outputs]

**Next Step:**
[What should happen next]

**Time Spent:** {{DURATION}}

## Blockers

[List of current impediments]

| Blocker ID | Description | Reported Date | Severity | Owner | Status | Resolution Plan |
|------------|-------------|---------------|----------|-------|--------|-----------------|
| {{ID}} | {{DESC}} | {{DATE}} | {{SEV}} | {{OWNER}} | {{STATUS}} | {{PLAN}} |

### Active Blockers

#### Blocker: {{BLOCKER_ID}}

**Description:**
[What is blocking progress]

**Impact:**
[How this affects the task]

**Severity:** Critical | High | Medium | Low

**Root Cause:**
[Why this blocker exists]

**Resolution Plan:**
[Steps to remove the blocker]

**Expected Resolution Date:** {{DATE}}

## Completion Criteria

[What must be true to mark the task complete]

### Required Criteria

- [ ] [Criterion 1]
- [ ] [Criterion 2]
- [ ] [Criterion 3]

### Optional Criteria (Nice to Have)

- [ ] [Criterion 1]
- [ ] [Criterion 2]

### Acceptance Sign-off

| Role | Name | Date | Sign-off |
|------|------|------|----------|
| Owner | {{NAME}} | {{DATE}} | Approved |
| Reviewer | {{NAME}} | {{DATE}} | {{STATUS}} |

## Progress Metrics

| Metric | Target | Current | Variance |
|--------|--------|---------|----------|
| Completion % | 100% | {{CURRENT}}% | {{VARIANCE}} |
| Days Elapsed | {{PLANNED}} | {{ACTUAL}} | {{VARIANCE}} |
| Estimated Remaining | - | {{DAYS}} | - |

### Burndown

| Date | Remaining Work | Completed | Total |
|------|----------------|-----------|-------|
| {{DATE}} | {{REMAINING}} | {{COMPLETED}} | {{TOTAL}} |

## Work Log

[Detailed time and effort tracking]

| Date | Agent | Activity | Duration | Notes |
|------|-------|----------|----------|-------|
| {{DATE}} | {{AGENT}} | {{ACTIVITY}} | {{DURATION}} | {{NOTES}} |

## Related Tasks

| Task ID | Relationship | Status |
|---------|--------------|--------|
| {{ID}} | {{RELATIONSHIP}} | {{STATUS}} |

## Artifacts

[Documents, code, or outputs produced]

| Artifact | Type | Location | Created Date |
|----------|------|----------|--------------|
| {{NAME}} | {{TYPE}} | {{PATH}} | {{DATE}} |

## Related Components

- [[component-framework/tasks/task-breakdown.md]] - For original task decomposition
- [[component-framework/tasks/task-priority.md]] - For priority information
- [[component-framework/memory/episodic-memory.md]] - For logging task completion
