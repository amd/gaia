---
template_id: status-report
template_type: documents
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Status report template for periodic project progress reporting
schema_version: "1.0"
---

# Status Report: {{PROJECT_NAME}}

## Purpose

This template provides a standardized format for periodic status reporting, including accomplishments, planned work, risks, and key metrics.

## Report Metadata

**Project Name:** {{PROJECT_NAME}}

**Report Period:** {{START_DATE}} to {{END_DATE}}

**Report Date:** {{DATE}}

**Reporting Period:** Week {{N}} | Sprint {{N}} | Month {{MONTH}}

**Prepared By:** {{AUTHOR}}

**Distribution:** {{LIST}}

## Overall Status

**RAG Status:** {{RED|AMBER|GREEN}}

### Status Summary

| Aspect | Status | Trend | Notes |
|--------|--------|-------|-------|
| Schedule | {{STATUS}} | {{TREND}} | {{NOTES}} |
| Budget | {{STATUS}} | {{TREND}} | {{NOTES}} |
| Scope | {{STATUS}} | {{TREND}} | {{NOTES}} |
| Quality | {{STATUS}} | {{TREND}} | {{NOTES}} |
| Resources | {{STATUS}} | {{TREND}} | {{NOTES}} |

### Executive Summary

[Brief overview of the reporting period - 2-3 paragraphs]

**Key Achievements:**
- [Achievement 1]
- [Achievement 2]

**Key Challenges:**
- [Challenge 1]
- [Challenge 2]

**Outlook:**
[Forward-looking statement about project trajectory]

## Completed This Period

[List of accomplishments during the reporting period]

### Deliverables Completed

| Deliverable | Description | Owner | Date Completed | Quality Status |
|-------------|-------------|-------|----------------|----------------|
| {{DELIVERABLE}} | {{DESC}} | {{OWNER}} | {{DATE}} | {{STATUS}} |

### Milestones Reached

| Milestone | Original Date | Actual Date | Variance | Notes |
|-----------|---------------|-------------|----------|-------|
| {{MILESTONE}} | {{DATE}} | {{DATE}} | {{VARIANCE}} | {{NOTES}} |

### Key Activities

| Activity | Status | Hours Spent | Contribution to Goals |
|----------|--------|-------------|----------------------|
| {{ACTIVITY}} | Complete | {{HOURS}} | {{CONTRIBUTION}} |

## Planned for Next Period

[Work planned for the upcoming reporting period]

### Planned Deliverables

| Deliverable | Description | Owner | Due Date | Dependencies |
|-------------|-------------|-------|----------|--------------|
| {{DELIVERABLE}} | {{DESC}} | {{OWNER}} | {{DATE}} | {{DEPS}} |

### Planned Milestones

| Milestone | Target Date | Confidence | Prerequisites |
|-----------|-------------|------------|---------------|
| {{MILESTONE}} | {{DATE}} | {{CONFIDENCE}} | {{PREREQS}} |

### Key Activities Planned

| Activity | Priority | Estimated Effort | Expected Outcome |
|----------|----------|-----------------|------------------|
| {{ACTIVITY}} | {{PRIORITY}} | {{EFFORT}} | {{OUTCOME}} |

## Risks and Issues

### Active Risks

| Risk ID | Description | Probability | Impact | Score | Owner | Mitigation Status |
|---------|-------------|-------------|--------|-------|-------|-------------------|
| {{ID}} | {{DESC}} | {{PROB}} | {{IMP}} | {{SCORE}} | {{OWNER}} | {{STATUS}} |

### Current Issues

| Issue ID | Description | Impact | Urgency | Owner | Status | Resolution Plan |
|----------|-------------|--------|---------|-------|--------|-----------------|
| {{ID}} | {{DESC}} | {{IMPACT}} | {{URG}} | {{OWNER}} | {{STATUS}} | {{PLAN}} |

### Risk Changes This Period

| Risk | Change | Reason |
|------|--------|--------|
| {{RISK}} | {{CHANGE}} | {{REASON}} |

## Metrics

### Delivery Metrics

| Metric | Target | Actual | Variance | Trend |
|--------|--------|--------|----------|-------|
| Velocity | {{TARGET}} | {{ACTUAL}} | {{VARIANCE}} | {{TREND}} |
| Sprint Goal Success Rate | {{TARGET}}% | {{ACTUAL}}% | {{VARIANCE}} | {{TREND}} |
| On-Time Delivery | {{TARGET}}% | {{ACTUAL}}% | {{VARIANCE}} | {{TREND}} |

### Quality Metrics

| Metric | Target | Actual | Variance | Trend |
|--------|--------|--------|----------|-------|
| Defect Density | {{TARGET}} | {{ACTUAL}} | {{VARIANCE}} | {{TREND}} |
| Test Coverage | {{TARGET}}% | {{ACTUAL}}% | {{VARIANCE}} | {{TREND}} |
| Code Review Coverage | {{TARGET}}% | {{ACTUAL}}% | {{VARIANCE}} | {{TREND}} |

### Resource Metrics

| Metric | Target | Actual | Variance | Trend |
|--------|--------|--------|----------|-------|
| Team Capacity | {{TARGET}} | {{ACTUAL}} | {{VARIANCE}} | {{TREND}} |
| Utilization | {{TARGET}}% | {{ACTUAL}}% | {{VARIANCE}} | {{TREND}} |
| Burn Rate | {{TARGET}} | {{ACTUAL}} | {{VARIANCE}} | {{TREND}} |

## Changes This Period

[Scope, schedule, or resource changes]

| Change | Type | Reason | Impact | Approval Status |
|--------|------|--------|--------|-----------------|
| {{CHANGE}} | {{TYPE}} | {{REASON}} | {{IMPACT}} | {{STATUS}} |

## Dependencies

| Dependency | Provider | Status | Expected Date | Risk |
|------------|----------|--------|---------------|------|
| {{DEP}} | {{PROVIDER}} | {{STATUS}} | {{DATE}} | {{RISK}} |

## Help Needed

[Escalations or assistance required]

| Item | From Whom | What's Needed | By When |
|------|-----------|---------------|---------|
| {{ITEM}} | {{WHO}} | {{WHAT}} | {{DATE}} |

## Appendix

### Glossary

| Term | Definition |
|------|------------|
| {{TERM}} | {{DEFINITION}} |

### References

- [Link to project plan]
- [Link to previous status report]
- [Link to risk register]

## Related Components

- [[component-framework/documents/meeting-notes.md]] - For meeting records
- [[component-framework/tasks/task-tracking.md]] - For task progress
- [[component-framework/memory/episodic-memory.md]] - For project history
