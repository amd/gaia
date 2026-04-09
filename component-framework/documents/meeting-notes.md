---
template_id: meeting-notes
template_type: documents
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Meeting notes template for capturing discussions, decisions, and action items
schema_version: "1.0"
---

# Meeting Notes: {{MEETING_NAME}}

## Purpose

This template captures meeting discussions, decisions made, and action items assigned during team or project meetings.

## Meeting Metadata

**Meeting Name:** {{MEETING_NAME}}

**Date:** {{DATE}}

**Time:** {{TIME}} {{TIMEZONE}}

**Location:** {{LOCATION}} (or Virtual Meeting Link)

**Meeting Type:** Standup | Planning | Review | Retrospective | Design Review | Other

**Facilitator:** {{FACILITATOR}}

**Note Taker:** {{NOTE_TAKER}}

## Attendees

### Present

| Name | Role | Team |
|------|------|------|
| {{NAME}} | {{ROLE}} | {{TEAM}} |

### Absent

| Name | Role | Reason |
|------|------|--------|
| {{NAME}} | {{ROLE}} | {{REASON}} |

### Guests

| Name | Organization | Purpose |
|------|--------------|---------|
| {{NAME}} | {{ORG}} | {{PURPOSE}} |

## Agenda

| Item | Topic | Presenter | Duration | Status |
|------|-------|-----------|----------|--------|
| 1 | {{TOPIC}} | {{PRESENTER}} | {{DURATION}} | Complete |
| 2 | {{TOPIC}} | {{PRESENTER}} | {{DURATION}} | Complete |

## Discussion

### Topic 1: {{TOPIC_NAME}}

**Presenter:** {{PRESENTER}}

**Summary:**
[Key points discussed]

**Key Points:**
- [Point 1]
- [Point 2]

**Concerns Raised:**
- [Concern 1]
- [Concern 2]

**Decisions Made:**
- [Decision 1]
- [Decision 2]

### Topic 2: {{TOPIC_NAME}}

[Continue with remaining topics...]

## Decisions Made

| Decision ID | Topic | Decision | Deciders | Vote (if applicable) |
|-------------|-------|----------|----------|---------------------|
| {{ID}} | {{TOPIC}} | {{DECISION}} | {{WHO}} | {{VOTE}} |

### Decision Details

#### Decision: {{DECISION_ID}}

**Context:**
[What prompted this decision]

**Options Considered:**
- [Option 1]
- [Option 2]

**Decision:**
[What was decided]

**Rationale:**
[Why this decision was made]

**Implications:**
[What this decision means for the project]

## Action Items

| Item ID | Description | Owner | Due Date | Priority | Status |
|---------|-------------|-------|----------|----------|--------|
| AI-1 | {{DESC}} | {{OWNER}} | {{DUE}} | {{PRIORITY}} | Open |
| AI-2 | {{DESC}} | {{OWNER}} | {{DUE}} | {{PRIORITY}} | Open |

### Action Item Details

#### Action Item: {{AI_ID}}

**Description:**
[What needs to be done]

**Owner:** {{OWNER}}

**Due Date:** {{DUE_DATE}}

**Priority:** P0 | P1 | P2 | P3

**Dependencies:**
- [Related action items or external dependencies]

**Success Criteria:**
[How we'll know this is complete]

## Follow-up Items

| Item | Owner | Notes |
|------|-------|-------|
| {{ITEM}} | {{OWNER}} | {{NOTES}} |

## Parking Lot

[Topics deferred to future meetings]

| Topic | Reason Deferred | Suggested Owner |
|-------|-----------------|-----------------|
| {{TOPIC}} | {{REASON}} | {{OWNER}} |

## Key Takeaways

[Most important points from the meeting]

1. [Takeaway 1]
2. [Takeaway 2]
3. [Takeaway 3]

## Next Meeting

**Proposed Date:** {{DATE}}

**Proposed Time:** {{TIME}}

**Agenda Items for Next Meeting:**
- [Item 1]
- [Item 2]

## Attachments

| Name | Type | Location |
|------|------|----------|
| {{NAME}} | {{TYPE}} | {{PATH}} |

## Related Components

- [[component-framework/documents/status-report.md]] - For periodic summaries
- [[component-framework/tasks/task-tracking.md]] - For tracking action items
- [[component-framework/memory/episodic-memory.md]] - For meeting history
