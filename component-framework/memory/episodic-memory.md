---
template_id: episodic-memory
template_type: memory
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Episodic memory template for historical execution records
schema_version: "1.0"
---

# Episodic Memory

## Purpose

This template maintains historical execution records for all significant agent activities. It provides an audit trail and reference for future similar executions.

## Execution Log

| Entry ID | Timestamp | Agent | Task | Phase | Outcome | Duration |
|----------|-----------|-------|------|-------|---------|----------|
| {{ID}} | {{TIMESTAMP}} | {{AGENT_ID}} | {{TASK}} | {{PHASE}} | {{OUTCOME}} | {{DURATION}} |

### Entry: {{ENTRY_ID}}

**Basic Information:**
- **Entry ID:** {{ENTRY_ID}}
- **Timestamp:** {{TIMESTAMP}}
- **Agent:** {{AGENT_ID}}
- **Pipeline Stage:** {{STAGE}}

**Task Details:**
- **Task Name:** {{TASK_NAME}}
- **Task Description:** {{TASK_DESCRIPTION}}
- **Priority:** P0 | P1 | P2 | P3
- **Dependencies:** [Related tasks this depended on]

**Execution Details:**
- **Start Time:** {{START_TIME}}
- **End Time:** {{END_TIME}}
- **Duration:** {{DURATION}}
- **Outcome:** Success | Partial | Failed
- **Status Code:** {{STATUS_CODE}}

## Significant Episodes

[Notable executions worth remembering]

### Episode: {{EPISODE_NAME}}

**Date:** {{DATE}}
**Agent:** {{AGENT_ID}}

**What Happened:**
[Description of the significant event]

**Why It Matters:**
[Explanation of significance]

**Lessons Learned:**
[Key takeaways from this episode]

**Related Artifacts:**
[Links to documents, code, or other outputs]

## Execution Statistics

| Metric | Value | Period |
|--------|-------|--------|
| Total Executions | {{COUNT}} | All time |
| Success Rate | {{RATE}}% | Last 30 days |
| Average Duration | {{DURATION}} | Last 30 days |
| Most Active Agent | {{AGENT}} | Last 30 days |

## Agent Activity Summary

| Agent | Executions | Successes | Failures | Avg Duration |
|-------|------------|-----------|----------|--------------|
| {{AGENT_ID}} | {{COUNT}} | {{SUCCESSES}} | {{FAILURES}} | {{DURATION}} |

## Artifact Registry

[Outputs produced during executions]

| Artifact ID | Type | Created By | Timestamp | Location | Description |
|-------------|------|------------|-----------|----------|-------------|
| {{ID}} | {{TYPE}} | {{AGENT}} | {{TIME}} | {{PATH}} | {{DESC}} |

## Error Log

[Recorded errors during execution]

| Timestamp | Agent | Error Type | Message | Resolution |
|-----------|-------|------------|---------|------------|
| {{TIME}} | {{AGENT}} | {{TYPE}} | {{MSG}} | {{RESOLUTION}} |

## Related Components

- [[component-framework/memory/working-memory.md]] - For active work that becomes history
- [[component-framework/memory/long-term-memory.md]] - For patterns extracted from episodes
- [[component-framework/documents/status-report.md]] - For periodic summaries
