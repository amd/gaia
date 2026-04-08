---
template_id: declarative-knowledge
template_type: knowledge
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Declarative knowledge template for facts, definitions, and concepts
schema_version: "1.0"
---

# Facts: {{SUBJECT_AREA}}

## Purpose

This template captures declarative knowledge including facts, definitions, assertions, and their relationships within a subject area.

## Subject Area Overview

**Subject:** {{SUBJECT_AREA}}

**Scope:**
[What this subject area covers]

**Last Updated:** {{TIMESTAMP}}

**Confidence Level:** [Overall confidence in this knowledge base]

## Assertions

[Fact statements with confidence scores]

### Assertion: {{ASSERTION_ID}}

- **Statement:** [The factual statement]
- **Confidence:** 0.0 - 1.0
- **Evidence:** [Supporting evidence]
- **Source:** [Where this fact comes from]
- **Date Asserted:** {{DATE}}
- **Last Validated:** {{DATE}}
- **Status:** Active | Disputed | Deprecated

### Assertion Registry

| ID | Statement | Confidence | Status | Last Validated |
|----|-----------|------------|--------|----------------|
| {{ID}} | {{STATEMENT}} | {{SCORE}} | {{STATUS}} | {{DATE}} |

## Relationships

[How facts relate to each other]

### Relationship: {{RELATIONSHIP_ID}}

- **Source Fact:** {{ASSERTION_ID}}
- **Relationship Type:** {{TYPE}}
- **Target Fact:** {{ASSERTION_ID}}
- **Description:** [Nature of the relationship]
- **Strength:** 0.0 - 1.0

### Relationship Types

| Type | Description | Example |
|------|-------------|---------|
| IMPLIES | One fact implies another | A implies B |
| CONTRADICTS | Facts are mutually exclusive | A contradicts B |
| DEPENDS_ON | One fact depends on another | A depends on B |
| GENERALIZES | One fact is more general | A generalizes B |
| SPECIALIZES | One fact is more specific | A specializes B |

## Sources

[Where each fact originated]

### Source: {{SOURCE_ID}}

- **Name:** [Source name]
- **Type:** Document | Person | System | Observation
- **URL/Location:** [Where to find it]
- **Credibility:** High | Medium | Low
- **Date Accessed:** {{DATE}}

### Source Registry

| Source ID | Name | Type | Credibility | Facts Supported |
|-----------|------|------|-------------|-----------------|
| {{ID}} | {{NAME}} | {{TYPE}} | {{CREDIBILITY}} | {{COUNT}} |

## Categories

[Classification of facts into categories]

| Category | Description | Fact Count |
|----------|-------------|------------|
| {{CATEGORY}} | {{DESC}} | {{COUNT}} |

## Definitions

[Key definitions in this subject area]

### Definition: {{TERM}}

- **Definition:** [Clear definition]
- **Context:** [Where this definition applies]
- **Related Terms:** [Synonyms, antonyms, related concepts]
- **Source:** [Authoritative source]

## Constraints

[Limits and boundaries of this knowledge]

- [Constraint 1]
- [Constraint 2]

## Open Questions

[Unresolved questions in this subject area]

| Question | Priority | Investigator | Status |
|----------|----------|--------------|--------|
| {{QUESTION}} | {{PRIORITY}} | {{WHO}} | {{STATUS}} |

## Related Components

- [[component-framework/knowledge/domain-knowledge.md]] - For broader domain context
- [[component-framework/knowledge/knowledge-graph.md]] - For structured relationships
- [[component-framework/knowledge/procedural-knowledge.md]] - For related procedures
