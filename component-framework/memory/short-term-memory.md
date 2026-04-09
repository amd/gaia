---
template_id: short-term-memory
template_type: memory
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Short-term memory template for immediate turn context and active working state
schema_version: "1.0"
---

# Short-Term Memory

## Purpose

This template holds immediate context for the current execution turn. It captures the active agent's working state, current task, and execution phase for continuity across turns.

## Current Turn Context

- **Timestamp:** {{TIMESTAMP}}
- **Active Agent:** {{AGENT_ID}}
- **Current Task:** {{TASK_DESCRIPTION}}
- **Execution Phase:** {{PHASE_NAME}}
- **Pipeline Stage:** {{STAGE_NAME}}

## Immediate State

[Agent populates with current working context]

```
State Description:
- Current focus area
- Active considerations
- Pending decisions
```

## Working Context

[Details about what the agent is currently working on]

### Current Objective

[Clear statement of what the agent is trying to accomplish this turn]

### Relevant Information

[Key information the agent is considering]

### Current Hypotheses

[Working theories or approaches being considered]

## Turn Output

[Agent records output produced this turn]

### Actions Taken

[List of actions executed this turn]

### Results Produced

[Outputs generated this turn]

### Next Steps

[What should happen in the next turn]

## Related Components

- [[component-framework/memory/working-memory.md]] - For extended reasoning trace
- [[component-framework/memory/episodic-memory.md]] - For logging this turn after completion
- [[component-framework/tasks/task-tracking.md]] - For updating task progress
