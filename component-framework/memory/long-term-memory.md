---
template_id: long-term-memory
template_type: memory
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Long-term memory template for persistent knowledge across sessions
schema_version: "1.0"
---

# Long-Term Memory

## Purpose

This template stores persistent knowledge that survives across sessions. It captures learned patterns, developed skills, and historical context that shapes future agent behavior.

## Learned Patterns

[Patterns discovered through repeated execution]

### Pattern: {{PATTERN_NAME}}

- **Discovery Date:** {{DATE}}
- **Discovering Agent:** {{AGENT_ID}}
- **Context:** When/where this pattern was identified
- **Description:** What the pattern does
- **Application:** How to apply this pattern
- **Validation Status:** Validated | Pending | Superseded

### Pattern Repository

| Pattern ID | Name | Domain | Status | Last Used |
|------------|------|--------|--------|-----------|
| {{ID}} | {{NAME}} | {{DOMAIN}} | {{STATUS}} | {{DATE}} |

## Skill Repository

[Capabilities developed over time]

### Skill: {{SKILL_NAME}}

- **Acquired Date:** {{DATE}}
- **Proficiency Level:** Beginner | Intermediate | Advanced | Expert
- **Prerequisites:** Skills required before this one
- **Related Skills:** Complementary capabilities
- **Usage Examples:** How this skill has been applied

### Skill Inventory

| Skill | Level | Times Used | Last Used |
|-------|-------|------------|-----------|
| {{SKILL}} | {{LEVEL}} | {{COUNT}} | {{DATE}} |

## Historical Context

[Significant events that shape future behavior]

### Milestone Events

| Date | Event | Impact | Agent |
|------|-------|--------|-------|
| {{DATE}} | {{EVENT}} | {{IMPACT}} | {{AGENT}} |

### Key Learnings

[Important insights gained during execution]

1. **Learning:** [Description]
   - **Source:** [Where it came from]
   - **Application:** [How it's been used]
   - **Confidence:** [High/Medium/Low]

## Decision History

[Major decisions made and their rationale]

### Decision: {{DECISION_NAME}}

- **Date Made:** {{DATE}}
- **Decision Makers:** {{AGENTS}}
- **Options Considered:** [List of alternatives]
- **Rationale:** Why this option was chosen
- **Outcome:** What happened as a result

## Related Components

- [[component-framework/memory/episodic-memory.md]] - For detailed execution history
- [[component-framework/knowledge/procedural-knowledge.md]] - For documented procedures
- [[component-framework/knowledge/domain-knowledge.md]] - For domain-specific knowledge
