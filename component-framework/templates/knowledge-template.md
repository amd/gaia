---
template_id: knowledge-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating knowledge base files
schema_version: "1.0"
---

# Knowledge Meta-Template

## Purpose

This meta-template provides the structure for generating knowledge base files. Knowledge components capture domain expertise, best practices, reference information, and conceptual understanding that agents use during execution.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{KNOWLEDGE_ID}} | Unique knowledge identifier | Yes | `domain-knowledge` |
| {{KNOWLEDGE_NAME}} | Human-readable knowledge name | Yes | `Domain Knowledge` |
| {{VERSION}} | Knowledge version (semver) | Yes | `1.0.0` |
| {{DOMAIN}} | Subject domain | Yes | `Software Engineering` |
| {{CATEGORY}} | Knowledge category | Yes | `procedural`, `declarative` |
| {{DESCRIPTION}} | Knowledge purpose | Yes | `Domain expertise reference` |
| {{CONCEPT_COUNT}} | Number of concepts | No | `5` |
| {{CONCEPT_DEFINITIONS}} | Concept details | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{KNOWLEDGE_ID}}
template_type: knowledge
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
domain: {{DOMAIN}}
category: {{CATEGORY}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Knowledge Body Template

```markdown
# {{KNOWLEDGE_NAME}}

## Purpose

[Describe what domain knowledge this component captures. Explain why this knowledge is important and when agents should reference it.]

## Knowledge Identity

| Attribute | Value |
|-----------|-------|
| Knowledge ID | {{KNOWLEDGE_ID}} |
| Domain | {{DOMAIN}} |
| Category | {{CATEGORY}} |
| Applicable Agents | [List of agents that use this] |

## Core Concepts

{{CONCEPT_DEFINITIONS}}

### Concept 1: {{CONCEPT_NAME}}

**Definition:**
[Clear, precise definition of the concept]

**Category:** [Type of concept]

**Key Characteristics:**
- Characteristic 1
- Characteristic 2
- Characteristic 3

**Related Concepts:**
- [[#Concept 2]] - Relationship description
- [[component-framework/knowledge/{{RELATED_KNOWLEDGE}}]] - Related knowledge

**Examples:**
```
{{EXAMPLE_1}}
```

### Concept 2: {{CONCEPT_NAME}}

[Continue for all concepts]

## Best Practices

### Practice 1: {{PRACTICE_NAME}}

**Description:**
[What the practice is]

**When to Apply:**
[Circumstances for using this practice]

**How to Apply:**
1. Step 1
2. Step 2
3. Step 3

**Example:**
```
{{PRACTICE_EXAMPLE}}
```

**Benefits:**
- Benefit 1
- Benefit 2

**Trade-offs:**
- Trade-off 1
- Trade-off 2

### Practice 2: {{PRACTICE_NAME}}

[Continue for all practices]

## Anti-Patterns

### Anti-Pattern 1: {{ANTI_PATTERN_NAME}}

**Description:**
[What the anti-pattern is]

**Why It's Problematic:**
[Problems it causes]

**Symptoms:**
- Symptom 1
- Symptom 2

**Alternative Approach:**
[Better way to do it]

**Example:**
```
{{ANTI_PATTERN_EXAMPLE}}
```

### Anti-Pattern 2: {{ANTI_PATTERN_NAME}}

[Continue for all anti-patterns]

## Reference Examples

### Example 1: {{EXAMPLE_NAME}}

```
{{EXAMPLE_CONTENT}}
```

**Explanation:**
[What this example demonstrates]

**Key Takeaways:**
- Takeaway 1
- Takeaway 2

### Example 2: {{EXAMPLE_NAME}}

[Continue for all examples]

## Decision Frameworks

### Framework 1: {{FRAMEWORK_NAME}}

**Purpose:**
[What decision this framework helps with]

**Decision Criteria:**
| Criterion | Weight | Description |
|-----------|--------|-------------|
| Criterion 1 | High | Description |
| Criterion 2 | Medium | Description |

**Decision Process:**
1. Step 1
2. Step 2
3. Step 3

**Output:**
[What decision output is produced]

## Common Questions

### Q: {{QUESTION}}?

**Answer:**
[Clear, concise answer]

**Explanation:**
[Detailed explanation if needed]

**Related:**
- [[#Concept 1]] - Related concept
- [[#Practice 1]] - Related practice

## Glossary

| Term | Definition |
|------|------------|
| Term 1 | Definition |
| Term 2 | Definition |

## Related Domains

- [[component-framework/knowledge/{{RELATED_DOMAIN}}]] - Related domain

## References

1. [Reference 1](url) - Description
2. [Reference 2](url) - Description

## Quality Indicators

High-quality knowledge demonstrates:
- [ ] Concepts are clearly defined
- [ ] Best practices are actionable
- [ ] Anti-patterns include alternatives
- [ ] Examples are relevant and clear
- [ ] References are authoritative

## Usage Examples

### Example 1: Agent Query

```
Agent: "What is {{CONCEPT_NAME}}?"
Knowledge: [Retrieved definition and examples]
```

### Example 2: Decision Support

```
Agent: "Should I use {{PRACTICE_NAME}}?"
Knowledge: [Decision framework applied]
Recommendation: Yes, because...
```
```

## Generation Instructions

### Step 1: Define Knowledge Scope

Articulate:
1. What domain this knowledge covers
2. What category (procedural vs declarative)
3. Which agents will use this knowledge

### Step 2: Identify Core Concepts

List:
- Key concepts in the domain
- Clear definitions for each
- Relationships between concepts
- Examples illustrating concepts

### Step 3: Document Best Practices

For each practice:
- Describe what it is
- When to apply it
- How to apply it
- Benefits and trade-offs

### Step 4: Document Anti-Patterns

For each anti-pattern:
- Describe the problematic pattern
- Explain why it's problematic
- List symptoms
- Provide alternative approach

### Step 5: Add Reference Examples

Include:
- Realistic examples
- Clear explanations
- Key takeaways

### Step 6: Validate Generated Knowledge

```python
# Load and validate the generated knowledge
loader = ComponentLoader()
knowledge = loader.load_component(f"knowledge/{{KNOWLEDGE_ID}}.md")
errors = loader.validate_component(f"knowledge/{{KNOWLEDGE_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify knowledge structure
assert knowledge['frontmatter']['domain'] == '{{DOMAIN}}'
assert knowledge['frontmatter']['category'] == '{{CATEGORY}}'
assert knowledge['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `knowledge`
- [ ] Domain is clearly defined
- [ ] Category is appropriate
- [ ] Concepts are well-defined
- [ ] Best practices are actionable
- [ ] Anti-patterns include alternatives
- [ ] Examples are relevant
- [ ] Related knowledge is correctly linked

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/templates/agent-definition.md]] - Agent definition template
- [[component-framework/knowledge/domain-knowledge.md]] - Domain knowledge example
- [[component-framework/knowledge/procedural-knowledge.md]] - Procedural knowledge example
