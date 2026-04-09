---
template_id: checklist-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating checklist definition files
schema_version: "1.0"
---

# Checklist Meta-Template

## Purpose

This meta-template provides the structure for generating checklist definition files. Checklists define quality validation criteria that agents use to verify work products, code, documentation, and processes.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{CHECKLIST_ID}} | Unique checklist identifier | Yes | `code-review-checklist` |
| {{CHECKLIST_NAME}} | Human-readable checklist name | Yes | `Code Review Checklist` |
| {{VERSION}} | Checklist version (semver) | Yes | `1.0.0` |
| {{SCOPE}} | What this checklist validates | Yes | `Source code quality` |
| {{DESCRIPTION}} | Checklist purpose | Yes | `Validates code quality` |
| {{CHECK_CATEGORIES}} | Categories of checks | Yes | `required, recommended, advisory` |
| {{CHECK_DEFINITIONS}} | Individual check details | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{CHECKLIST_ID}}
template_type: checklists
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
scope: {{SCOPE}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Checklist Body Template

```markdown
# {{CHECKLIST_NAME}}

## Purpose

[Describe what this checklist validates and when it should be used. Explain the scope and importance of the validation.]

## Checklist Identity

| Attribute | Value |
|-----------|-------|
| Checklist ID | {{CHECKLIST_ID}} |
| Scope | {{SCOPE}} |
| Trigger | [When this checklist is activated] |
| Total Checks | {{TOTAL_CHECKS}} |

## Check Categories

This checklist organizes checks into three categories:

| Category | Requirement | Pass Threshold |
|----------|-------------|----------------|
| **Required** | Must pass | 100% |
| **Recommended** | Should pass | >= 80% |
| **Advisory** | Nice to have | >= 50% |

## Check Definitions

{{CHECK_DEFINITIONS}}

### Required Checks

All required checks must pass for overall validation to succeed.

| ID | Check | Pass Criteria | Verification Method | Category |
|----|-------|---------------|---------------------|----------|
| R1 | {{CHECK_NAME}} | [Specific criteria] | [How to verify] | {{CATEGORY}} |
| R2 | {{CHECK_NAME}} | [Specific criteria] | [How to verify] | {{CATEGORY}} |

#### R1: {{CHECK_NAME}}

**Description:** [What this check validates]

**Pass Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2

**Verification Method:**
```
{{VERIFICATION_COMMAND_OR_PROCESS}}
```

**Common Failure Causes:**
- Cause 1
- Cause 2

**Remediation:**
1. Step 1
2. Step 2

### Recommended Checks

Majority of recommended checks should pass.

| ID | Check | Pass Criteria | Priority |
|----|-------|---------------|----------|
| C1 | {{CHECK_NAME}} | [Specific criteria] | High |
| C2 | {{CHECK_NAME}} | [Specific criteria] | Medium |

#### C1: {{CHECK_NAME}}

**Description:** [What this check validates]

**Pass Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2

**Why Recommended:** [Why this is recommended vs required]

**Improvement Steps:**
1. Step 1
2. Step 2

### Advisory Checks

Informational checks for continuous improvement.

| ID | Check | Suggestion | Impact |
|----|-------|------------|--------|
| A1 | {{CHECK_NAME}} | [Improvement idea] | Low/Medium/High |

#### A1: {{CHECK_NAME}}

**Description:** [What this check suggests]

**Suggestion:**
[Detailed improvement suggestion]

**Impact:**
[What improvement would achieve]

## Pass/Fail Decision Logic

### Overall Pass

**Conditions:**
- All required checks pass (100%)
- >= 80% of recommended checks pass
- Any number of advisory checks can fail

### Conditional Pass

**Conditions:**
- All required checks pass (100%)
- >= 50% of recommended checks pass
- Any number of advisory checks can fail

**Actions Required:**
- Document which recommended checks failed
- Create remediation plan

### Fail

**Conditions:**
- Any required check fails, OR
- < 50% of recommended checks pass

**Actions Required:**
- Stop execution
- Fix all failed required checks
- Create remediation plan for recommended checks
- Re-run full checklist

## Scoring System

### Calculation

```
Score = (Required_Passed / Required_Total * 0.6) +
        (Recommended_Passed / Recommended_Total * 0.3) +
        (Advisory_Passed / Advisory_Total * 0.1)
```

### Score Interpretation

| Score | Grade | Status |
|-------|-------|--------|
| 0.9 - 1.0 | A | Pass |
| 0.8 - 0.9 | B | Conditional Pass |
| 0.7 - 0.8 | C | Marginal |
| < 0.7 | F | Fail |

## Integration with Component Framework

### Components Validated

This checklist may validate:
- `src/**/*.py` - Python source files
- `documents/{{DOCUMENT_NAME}}.md` - Documentation

### Components Updated

This checklist may update:
- `tasks/task-tracking.md` - Validation status
- `memory/working-memory.md` - Quality metrics

## Usage Examples

### Example 1: Full Validation

```
Checklist: {{CHECKLIST_ID}}
Required: 10/10 passed (100%)
Recommended: 8/10 passed (80%)
Advisory: 3/5 passed (60%)
Overall: PASS (Score: 0.90)
```

### Example 2: Failed Validation

```
Checklist: {{CHECKLIST_ID}}
Required: 8/10 passed (80%) - FAIL
Recommended: 10/10 passed (100%)
Advisory: 5/5 passed (100%)
Overall: FAIL (R3, R7 failed)
```

## Related Checklists

- [[component-framework/checklists/{{RELATED_CHECKLIST}}]] - Related checklist

## Quality Criteria for Checklist

- [ ] Each check has clear pass criteria
- [ ] Verification methods are specific
- [ ] Remediation steps are actionable
- [ ] Categories are appropriately assigned
- [ ] Decision logic is unambiguous

## References

- [[component-framework/templates/component-template.md]] - Component template
- [[component-framework/templates/task-template.md]] - Task template
- [[component-framework/checklists/domain-analysis-checklist.md]] - Domain analysis example
```

## Generation Instructions

### Step 1: Define Checklist Scope

Articulate:
1. What work product is being validated
2. When this checklist should be used
3. What quality standards apply

### Step 2: Identify Required Checks

List checks that:
- Are essential for quality
- Must pass for validation to succeed
- Have clear, measurable criteria

### Step 3: Identify Recommended Checks

List checks that:
- Represent best practices
- Should pass but not critical
- Improve overall quality

### Step 4: Identify Advisory Checks

List checks that:
- Are nice-to-have improvements
- Don't affect pass/fail
- Provide continuous improvement guidance

### Step 5: Define Decision Logic

Specify:
- Pass conditions
- Conditional pass conditions
- Fail conditions
- Remediation requirements

### Step 6: Validate Generated Checklist

```python
# Load and validate the generated checklist
loader = ComponentLoader()
checklist = loader.load_component(f"checklists/{{CHECKLIST_ID}}.md")
errors = loader.validate_component(f"checklists/{{CHECKLIST_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify checklist structure
assert checklist['frontmatter']['scope'] == '{{SCOPE}}'
assert checklist['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `checklists`
- [ ] Scope is clearly defined
- [ ] All checks have pass criteria
- [ ] Verification methods are specific
- [ ] Remediation steps are actionable
- [ ] Decision logic is complete
- [ ] Related checklists are correctly linked

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/templates/task-template.md]] - Task template
- [[component-framework/checklists/domain-analysis-checklist.md]] - Domain analysis example
- [[component-framework/checklists/code-review-checklist.md]] - Code review example
