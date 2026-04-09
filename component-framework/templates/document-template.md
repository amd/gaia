---
template_id: document-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating document definition files
schema_version: "1.0"
---

# Document Meta-Template

## Purpose

This meta-template provides the structure for generating document definition files. Document components define structured document templates that agents create, read, and update during execution, including design docs, API specs, meeting notes, and status reports.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{DOCUMENT_ID}} | Unique document identifier | Yes | `design-doc` |
| {{DOCUMENT_NAME}} | Human-readable document name | Yes | `Design Document` |
| {{VERSION}} | Document version (semver) | Yes | `1.0.0` |
| {{DOCUMENT_TYPE}} | Type of document | Yes | `design`, `spec`, `notes`, `report` |
| {{DESCRIPTION}} | Document purpose | Yes | `Design documentation` |
| {{SECTION_COUNT}} | Number of sections | No | `5` |
| {{SECTION_DEFINITIONS}} | Section details | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{DOCUMENT_ID}}
template_type: documents
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
document_type: {{DOCUMENT_TYPE}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Document Body Template

```markdown
# {{DOCUMENT_NAME}}

## Purpose

[Describe what type of document this template supports and when it should be used. Explain the document's role in the development process.]

## Document Identity

| Attribute | Value |
|-----------|-------|
| Document ID | {{DOCUMENT_ID}} |
| Type | {{DOCUMENT_TYPE}} |
| Sections | {{SECTION_COUNT}} |
| Format | Markdown with structured sections |

## Document Structure

{{SECTION_DEFINITIONS}}

### Section 1: {{SECTION_1_NAME}}

**Purpose:**
[Why this section exists and what it captures]

**Required Content:**
- Content item 1
- Content item 2

**Optional Content:**
- Optional item 1
- Optional item 2

**Template:**
```markdown
## {{SECTION_1_NAME}}

[Content goes here]

### Subsection 1.1

[Detailed content]
```

**Quality Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2

### Section 2: {{SECTION_2_NAME}}

[Continue for all sections]

## Document Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Document title |
| `author` | string | Yes | Document author |
| `created` | date | Yes | Creation date |
| `status` | string | Yes | draft/review/final |
| `version` | string | Yes | Document version |

## Document Quality Criteria

### Structure Quality

- [ ] All required sections present
- [ ] Sections follow template guidance
- [ ] Hierarchy is logical (H1 → H2 → H3)
- [ ] Cross-references are valid

### Content Quality

- [ ] Content is clear and unambiguous
- [ ] Technical details are accurate
- [ ] Examples are relevant and correct
- [ ] Diagrams/tables support content

### Completeness Quality

- [ ] No placeholder text remains
- [ ] All TBD items resolved
- [ ] Appendices include referenced materials
- [ ] Glossary defines technical terms

## Integration with Component Framework

### Documents Created By

- [[component-framework/personas/{{AGENT_NAME}}]] - Agent that creates
- [[component-framework/workflows/{{WORKFLOW_NAME}}]] - Workflow that creates

### Documents Updated By

- [[component-framework/personas/{{AGENT_NAME}}]] - Agent that updates
- [[component-framework/tasks/{{TASK_NAME}}]] - Task that updates

### Documents Consumed By

- [[component-framework/personas/{{AGENT_NAME}}]] - Agent that reads
- [[component-framework/workflows/{{WORKFLOW_NAME}}]] - Workflow that reads

## Usage Examples

### Example 1: New Document

```
Agent: Creating document using {{DOCUMENT_ID}} template
Title: "System Architecture"
Sections: 5
Status: draft

Agent: Document created at documents/system-architecture.md
```

### Example 2: Document Update

```
Agent: Updating document documents/system-architecture.md
Section: "Component Overview"
Changes: Added new component descriptions

Agent: Document updated. Version: 1.0.0 → 1.1.0
```

## Document Lifecycle

### Creation

**Trigger:** [Event that triggers document creation]
**Process:**
1. Select document template
2. Populate metadata
3. Fill required sections
4. Set initial status (draft)

### Review

**Trigger:** [Event that triggers review]
**Process:**
1. Mark status as "review"
2. Collect feedback
3. Address feedback items
4. Update document

### Finalization

**Trigger:** [Event that triggers finalization]
**Process:**
1. Verify all sections complete
2. Mark status as "final"
3. Lock for edits (unless major revision)
4. Archive previous versions

### Revision

**Trigger:** [Event that triggers revision]
**Process:**
1. Create new version
2. Update version number
3. Document changes
4. Notify stakeholders

## Related Documents

- [[component-framework/documents/{{RELATED_DOCUMENT}}]] - Related document

## Quality Indicators

High-quality documents demonstrate:
- [ ] Clear, organized structure
- [ ] Complete required sections
- [ ] Accurate technical content
- [ ] Useful for intended audience
- [ ] Maintained over time

## References

- [[component-framework/templates/component-template.md]] - Component template
- [[component-framework/documents/design-doc.md]] - Design document example
- [[component-framework/documents/api-spec.md]] - API spec example
```

## Generation Instructions

### Step 1: Define Document Purpose

Articulate:
1. What type of document this template supports
2. When this document type is needed
3. Who the audience is

### Step 2: Specify Document Structure

Define:
- All required sections
- Section purposes and content guidance
- Any subsections needed

### Step 3: Define Metadata

Specify:
- Required metadata fields
- Field types and validation
- Default values if applicable

### Step 4: Establish Quality Criteria

Define:
- Structure quality criteria
- Content quality criteria
- Completeness criteria

### Step 5: Document Lifecycle

Specify:
- Creation process
- Review process
- Finalization process
- Revision process

### Step 6: Validate Generated Document

```python
# Load and validate the generated document template
loader = ComponentLoader()
doc = loader.load_component(f"documents/{{DOCUMENT_ID}}.md")
errors = loader.validate_component(f"documents/{{DOCUMENT_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify document structure
assert doc['frontmatter']['document_type'] == '{{DOCUMENT_TYPE}}'
assert doc['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `documents`
- [ ] `document_type` is valid
- [ ] All sections have clear purpose
- [ ] Quality criteria are specific
- [ ] Document lifecycle is complete
- [ ] Integration is documented
- [ ] Related documents are correctly linked

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/templates/checklist-template.md]] - Checklist template for validation
- [[component-framework/documents/design-doc.md]] - Design document example
- [[component-framework/documents/api-spec.md]] - API spec example
