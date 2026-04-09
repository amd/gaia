---
template_id: persona-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating agent persona files
schema_version: "1.0"
---

# Persona Meta-Template

## Purpose

This meta-template provides the structure for generating agent persona files. Personas define the role identity, communication style, and behavioral patterns for agents within the ecosystem.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{PERSONA_ID}} | Unique persona identifier | Yes | `specialist-agent` |
| {{PERSONA_NAME}} | Human-readable persona name | Yes | `Specialist Agent` |
| {{VERSION}} | Persona version (semver) | Yes | `1.0.0` |
| {{ROLE_TITLE}} | Professional role title | Yes | `Domain Specialist` |
| {{DESCRIPTION}} | Persona purpose | Yes | `Executes specialized tasks` |
| {{COMMUNICATION_STYLE}} | Communication approach | Yes | `direct, technical` |
| {{TONE}} | Interaction tone | Yes | `professional, helpful` |
| {{PRIMARY_RESPONSIBILITIES}} | List of responsibilities | Yes | See body template |
| {{BEHAVIORAL_PATTERNS}} | List of behavior patterns | Yes | See body template |
| {{EXPERTISE_DOMAINS}} | List of expertise areas | Yes | See body template |
| {{CONSTRAINTS}} | Behavioral constraints | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{PERSONA_ID}}
template_type: personas
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Persona Body Template

```markdown
# {{PERSONA_NAME}} — {{ROLE_TITLE}}

## Identity

**Persona ID:** {{PERSONA_ID}}
**Role:** {{ROLE_TITLE}}
**Archetype:** [e.g., Specialist, Coordinator, Validator, Explorer]

## Purpose

[Describe the core purpose of this persona. What role does it serve in the agent ecosystem? When is this persona activated?]

## Communication Profile

| Attribute | Value |
|-----------|-------|
| Style | {{COMMUNICATION_STYLE}} |
| Tone | {{TONE}} |
| Formality | [formal/casual/adaptable] |
| verbosity | [concise/detailed/adaptable] |
| Emoji Usage | [none/minimal/moderate] |

## Primary Responsibilities

{{PRIMARY_RESPONSIBILITIES}}
- Responsibility 1
- Responsibility 2
- Responsibility 3

## Behavioral Patterns

{{BEHAVIORAL_PATTERNS}}
- **Pattern 1:** Description of when and how this pattern manifests
- **Pattern 2:** Description of behavioral tendency
- **Pattern 3:** Description of response pattern

## Expertise Domains

{{EXPERTISE_DOMAINS}}
- **Domain 1:** Description of expertise level and scope
- **Domain 2:** Description of expertise level and scope
- **Domain 3:** Description of expertise level and scope

## Constraints

{{CONSTRAINTS}}
- **Constraint 1:** [Hard/Soft] - Description of limitation
- **Constraint 2:** [Hard/Soft] - Description of limitation
- **Boundary 1:** What this persona does NOT do

## Decision-Making Framework

### High-Confidence Decisions

[Describe types of decisions this persona can make autonomously]

### Escalation Triggers

[Describe when this persona should escalate or seek assistance]

### Risk Tolerance

[Describe the persona's approach to risk: risk-averse, risk-neutral, or risk-seeking]

## Interaction Patterns

### With Users

[Describe how this persona interacts with end users]

### With Other Agents

[Describe how this persona coordinates with other agents]

### With Components

[Describe how this persona reads/writes/updates component-framework components]

## Quality Indicators

High-quality persona execution demonstrates:
- [ ] Indicator 1
- [ ] Indicator 2
- [ ] Indicator 3

## Related Personas

- [[component-framework/personas/{{RELATED_PERSONA}}]] - Related persona
- [[component-framework/templates/agent-definition.md]] - Agent that uses this persona

## Usage Examples

### Example 1: Standard Interaction

```
User: [Typical user input]
{{PERSONA_NAME}}: [Example response demonstrating persona]
```

### Example 2: Complex Scenario

```
User: [Complex user input]
{{PERSONA_NAME}}: [Example response showing decision-making]
```

## Activation Triggers

This persona is activated when:
- Keywords: [list, of, keywords]
- Phases: [LIST, OF, PHASES]
- Context: [Situational triggers]
```

## Generation Instructions

### Step 1: Define Persona Purpose

Clearly articulate:
1. What role this persona serves
2. When it should be activated
3. What makes it distinct from other personas

### Step 2: Specify Communication Profile

Define:
- Communication style (direct, collaborative, analytical, etc.)
- Tone (professional, friendly, technical, etc.)
- Formality level
- Verbosity preference

### Step 3: Document Responsibilities and Behaviors

List:
- 3-5 primary responsibilities
- 3-5 behavioral patterns
- 2-4 expertise domains
- 2-4 constraints/boundaries

### Step 4: Define Decision Framework

Specify:
- What decisions are autonomous
- What requires escalation
- Risk tolerance level

### Step 5: Validate Generated Persona

```python
# Load and validate the generated persona
loader = ComponentLoader()
persona = loader.load_component(f"personas/{{PERSONA_ID}}.md")
errors = loader.validate_component(f"personas/{{PERSONA_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify persona loads correctly
assert persona['frontmatter']['template_type'] == 'personas'
assert persona['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `personas`
- [ ] `version` follows semver format
- [ ] Communication profile is complete
- [ ] Responsibilities are specific and actionable
- [ ] Behavioral patterns are observable
- [ ] Expertise domains are clearly scoped
- [ ] Constraints include both hard and soft limits
- [ ] Decision framework specifies escalation triggers
- [ ] Related personas are correctly linked

## Related Components

- [[component-framework/templates/agent-definition.md]] - Agent definition template
- [[component-framework/templates/workflow-template.md]] - Workflow template
- [[component-framework/personas/pipeline-agent.md]] - Pipeline agent persona
- [[component-framework/personas/specialist-agent.md]] - Specialist agent persona
