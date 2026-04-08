---
template_id: component-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating ecosystem component files
schema_version: "1.0"
---

# Component Template Meta-Template

## Purpose

This meta-template provides the structure for generating ecosystem component files. Components are supporting artifacts that agents use during execution, including commands, tasks, checklists, knowledge bases, and utilities.

## Component Types

| Type | Directory | Purpose | Example |
|------|-----------|---------|---------|
| `command` | `commands/` | Executable command definitions | `shell-commands.md` |
| `task` | `tasks/` | Multi-step workflow definitions | `task-breakdown.md` |
| `checklist` | `checklists/` | Quality validation checklists | `code-review-checklist.md` |
| `knowledge` | `knowledge/` | Domain knowledge entries | `domain-knowledge.md` |
| `document` | `documents/` | Structured document templates | `design-doc.md` |
| `memory` | `memory/` | Memory component templates | `working-memory.md` |

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{COMPONENT_ID}} | Unique component identifier | Yes | `task-breakdown` |
| {{COMPONENT_NAME}} | Human-readable component name | Yes | `Task Breakdown` |
| {{COMPONENT_TYPE}} | Component type from table above | Yes | `tasks` |
| {{VERSION}} | Component version (semver) | Yes | `1.0.0` |
| {{DESCRIPTION}} | Component purpose | Yes | `Breaks down complex tasks` |
| {{PARENT_AGENT}} | Owning/using agent | No | `domain-analyzer` |
| {{TRIGGER_CONDITION}} | When component activates | No | `During task analysis` |
| {{INPUT_SPEC}} | Expected inputs | No | `task: string` |
| {{OUTPUT_SPEC}} | Produced outputs | No | `subtasks: list` |
| {{CONTENT_BODY}} | Main component content | Yes | Markdown body |

## Frontmatter Template

```yaml
---
template_id: {{COMPONENT_ID}}
template_type: {{COMPONENT_TYPE}}
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Component Body Templates by Type

### Command Component Template

```markdown
# {{COMPONENT_NAME}}

## Purpose

[Describe what this command does and when it should be used.]

## Command Syntax

```
{{COMMAND_SYNTAX}}
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--param1` | string | Yes | Description |
| `--param2` | int | No | Description |

## Execution Steps

1. **Step 1:** [Describe first step]
2. **Step 2:** [Describe second step]
3. **Step 3:** [Describe final step]

## Preconditions

- [ ] Precondition 1
- [ ] Precondition 2

## Output Format

[Describe the output produced by this command.]

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| Error message | What causes it | How to fix |

## Examples

### Example 1: Basic Usage

```bash
{{EXAMPLE_COMMAND_1}}
```

**Expected Output:**
```
{{EXAMPLE_OUTPUT_1}}
```

## Related Components

- [[component-framework/{{RELATED_COMPONENT}}]] - Related component
```

### Task Component Template

```markdown
# {{COMPONENT_NAME}}

## Purpose

[Describe what this task workflow accomplishes.]

## Task Identity

- **Task ID:** {{TASK_ID}}
- **Owner Agent:** {{OWNER_AGENT}}
- **Trigger Condition:** {{TRIGGER_CONDITION}}

## Input Requirements

| Input | Type | Description |
|-------|------|-------------|
| `task` | string | Task description |
| `context` | dict | Additional context |

## Workflow Steps

### Step 1: {{STEP_1_NAME}}

**Purpose:** [Why this step exists]

**Activities:**
1. Activity 1
2. Activity 2

**Success Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2

### Step 2: {{STEP_2_NAME}}

[Continue for all steps]

## Output Specification

| Output | Type | Description |
|--------|------|-------------|
| `result` | dict | Task result |

## Failure Handling

| Failure Mode | Detection | Recovery |
|--------------|-----------|----------|
| Failure type | How detected | How to recover |

## Related Components

- [[component-framework/{{RELATED_COMPONENT}}]] - Related component
```

### Checklist Component Template

```markdown
# {{COMPONENT_NAME}}

## Purpose

[Describe what this checklist validates.]

## Checklist Identity

- **Checklist ID:** {{CHECKLIST_ID}}
- **Scope:** {{SCOPE}}
- **Trigger:** {{TRIGGER_CONDITION}}

## Required Checks

All required checks must pass for overall validation to succeed.

| ID | Check | Pass Criteria | Verification Method |
|----|-------|---------------|---------------------|
| R1 | Check name | Criteria | How to verify |
| R2 | Check name | Criteria | How to verify |

## Recommended Checks

Majority of recommended checks should pass.

| ID | Check | Pass Criteria | Priority |
|----|-------|---------------|----------|
| C1 | Check name | Criteria | High |
| C2 | Check name | Criteria | Medium |

## Advisory Checks

Informational checks for continuous improvement.

| ID | Check | Suggestion |
|----|-------|------------|
| A1 | Check name | Improvement idea |

## Pass/Fail Decision Logic

**Overall Pass:** All required checks pass AND >= 80% of recommended checks pass.

**Conditional Pass:** All required checks pass AND >= 50% of recommended checks pass.

**Fail:** Any required check fails OR < 50% of recommended checks pass.

## Related Components

- [[component-framework/{{RELATED_COMPONENT}}]] - Related component
```

### Knowledge Base Component Template

```markdown
# {{COMPONENT_NAME}}

## Purpose

[Describe the domain knowledge this component captures.]

## Domain Identity

- **Domain:** {{DOMAIN_NAME}}
- **Category:** {{CATEGORY}}
- **Applicable Agents:** {{APPLICABLE_AGENTS}}

## Core Concepts

### Concept 1: {{CONCEPT_NAME}}

**Definition:** [Clear definition]

**Key Characteristics:**
- Characteristic 1
- Characteristic 2

**Related Concepts:**
- [[#Concept 2]]
- [[component-framework/{{RELATED_COMPONENT}}]]

### Concept 2: {{CONCEPT_NAME}}

[Continue for all concepts]

## Best Practices

### Practice 1: {{PRACTICE_NAME}}

**Description:** [What the practice is]

**When to Apply:** [Circumstances for use]

**How to Apply:**
1. Step 1
2. Step 2
3. Step 3

**Example:**
```
{{PRACTICE_EXAMPLE}}
```

## Anti-Patterns

### Anti-Pattern 1: {{ANTI_PATTERN_NAME}}

**Description:** [What the anti-pattern is]

**Why It's Problematic:** [Problems it causes]

**Alternative Approach:** [Better way to do it]

## Reference Examples

### Example 1: {{EXAMPLE_NAME}}

```
{{EXAMPLE_CONTENT}}
```

**Explanation:** [What this example demonstrates]

## Related Domains

- [[component-framework/knowledge/{{RELATED_DOMAIN}}]] - Related domain

## References

1. [Reference 1](url)
2. [Reference 2](url)
```

### Document Component Template

```markdown
# {{COMPONENT_NAME}}

## Purpose

[Describe what type of document this template supports.]

## Document Metadata

| Field | Value |
|-------|-------|
| Document ID | {{DOCUMENT_ID}} |
| Document Type | {{DOCUMENT_TYPE}} |
| Author | {{AUTHOR}} |
| Created | {{CREATED_DATE}} |
| Version | {{VERSION}} |

## Document Structure

### 1. Executive Summary

[Template guidance for this section]

{{EXECUTIVE_SUMMARY_GUIDANCE}}

### 2. Background

[Template guidance for this section]

{{BACKGROUND_GUIDANCE}}

### 3. Main Content

[Template guidance for this section]

{{MAIN_CONTENT_GUIDANCE}}

### 4. Conclusions

[Template guidance for this section]

{{CONCLUSIONS_GUIDANCE}}

### 5. Appendices

[Template guidance for appendices]

{{APPENDICES_GUIDANCE}}

## Document Quality Criteria

- [ ] Structure follows template
- [ ] All required sections present
- [ ] Content is clear and complete
- [ ] References are cited

## Related Components

- [[component-framework/{{RELATED_COMPONENT}}]] - Related component
```

## Generation Instructions

### Step 1: Identify Component Type

Determine which type of component is needed:
- Command: For executable operations
- Task: For multi-step workflows
- Checklist: For quality validation
- Knowledge: For domain reference
- Document: For structured documents

### Step 2: Select Appropriate Template

Choose the body template that matches the component type.

### Step 3: Populate Variables

Fill in all `{{VARIABLE}}` placeholders:
- Use meaningful, descriptive names
- Ensure IDs are unique within the component-framework
- Follow naming conventions (lowercase, hyphenated)

### Step 4: Validate Generated Component

```python
# Load and validate the generated component
loader = ComponentLoader()
component = loader.load_component("{{COMPONENT_TYPE}}/{{COMPONENT_ID}}.md")
errors = loader.validate_component("{{COMPONENT_TYPE}}/{{COMPONENT_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` matches directory name
- [ ] `version` follows semver format
- [ ] Component body has meaningful content
- [ ] Related component links are valid
- [ ] Component serves a clear purpose
- [ ] Component is reusable (not task-specific)

## Related Components

- [[component-framework/templates/agent-definition.md]] - Agent generation template
- [[component-framework/templates/ecosystem-config.md]] - Ecosystem configuration template
