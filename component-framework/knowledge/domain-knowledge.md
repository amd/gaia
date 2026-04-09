---
template_id: domain-knowledge
template_type: knowledge
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Domain knowledge template for domain-specific reference material
schema_version: "1.0"
---

# Domain Knowledge: {{DOMAIN_NAME}}

## Purpose

This template captures domain-specific knowledge including core concepts, terminology, best practices, and reference implementations for the specified domain.

## Domain Overview

**Domain Name:** {{DOMAIN_NAME}}

**Description:**
[Brief overview of what this domain covers]

**Scope:**
[What is included and excluded from this domain]

**Related Domains:**
[List of adjacent or dependent domains]

## Core Concepts

[Key concepts and definitions fundamental to this domain]

### Concept: {{CONCEPT_NAME}}

- **Definition:** [Clear, concise definition]
- **Category:** [Type or classification]
- **Related Concepts:** [Links to related concepts]
- **Importance:** Critical | High | Medium | Low

## Terminology

| Term | Definition | Related Terms | Source |
|------|------------|---------------|--------|
| {{TERM}} | {{DEFINITION}} | {{RELATED}} | {{SOURCE}} |

### Term Details

#### {{TERM}}

- **Definition:** [Full definition]
- **Context:** [When/how this term is used]
- **Examples:** [Usage examples]
- **Anti-examples:** [Common misuses]

## Best Practices

[Validated approaches for this domain]

### Practice: {{PRACTICE_NAME}}

- **Description:** [What this practice entails]
- **When to Apply:** [Situations where this is useful]
- **How to Apply:** [Step-by-step application]
- **Evidence:** [Why this is considered a best practice]
- **Related Practices:** [Connected practices]

## Anti-Patterns

[What to avoid and why]

### Anti-Pattern: {{ANTI_PATTERN_NAME}}

- **Description:** [What this anti-pattern looks like]
- **Why It's Bad:** [Negative consequences]
- **Symptoms:** [How to recognize it]
- **Solution:** [What to do instead]
- **Related Anti-Patterns:** [Connected issues]

## Reference Implementations

[Code examples and patterns]

### Implementation: {{IMPLEMENTATION_NAME}}

**Location:** `{{FILE_PATH}}`

**Purpose:**
[What this implementation does]

**Key Components:**
- [Component 1]
- [Component 2]

**Usage Example:**
```{{LANGUAGE}}
{{CODE_EXAMPLE}}
```

## Domain Entities

[Key entities in this domain]

| Entity | Type | Description | Attributes |
|--------|------|-------------|------------|
| {{ENTITY}} | {{TYPE}} | {{DESC}} | {{ATTRIBUTES}} |

## Common Operations

[Frequently performed operations in this domain]

| Operation | Description | Input | Output | Complexity |
|-----------|-------------|-------|--------|------------|
| {{OP}} | {{DESC}} | {{INPUT}} | {{OUTPUT}} | {{COMPLEXITY}} |

## Knowledge Gaps

[Areas where knowledge is incomplete]

- [ ] {{GAP_1}}
- [ ] {{GAP_2}}

## Related Components

- [[component-framework/knowledge/procedural-knowledge.md]] - For procedures in this domain
- [[component-framework/knowledge/declarative-knowledge.md]] - For facts about this domain
- [[component-framework/checklists/domain-analysis-checklist.md]] - For domain analysis
