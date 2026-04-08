---
template_id: agent-definition
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating new agent definition files
schema_version: "1.0"
---

# Agent Definition Meta-Template

## Purpose

This meta-template provides the structure for generating new agent definition files. Use this template when creating agents through the ecosystem builder or manual agent development.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{AGENT_ID}} | Unique agent identifier | Yes | `domain-analyzer` |
| {{AGENT_NAME}} | Human-readable agent name | Yes | `Domain Analyzer` |
| {{VERSION}} | Agent version (semver) | Yes | `1.0.0` |
| {{CATEGORY}} | Agent category | Yes | `analysis` |
| {{DESCRIPTION}} | Agent purpose description | Yes | `Analyzes input tasks...` |
| {{MODEL_ID}} | Target LLM model | No | `Qwen3.5-35B-A3B-GGUF` |
| {{KEYWORDS_LIST}} | Trigger keywords | Yes | `- analyze\n- domain` |
| {{PHASES_LIST}} | Trigger phases | Yes | `- DOMAIN_ANALYSIS` |
| {{COMPLEXITY_MIN}} | Min complexity score | Yes | `0.0` |
| {{COMPLEXITY_MAX}} | Max complexity score | Yes | `1.0` |
| {{CAPABILITIES_LIST}} | Agent capabilities | Yes | `- domain-analysis` |
| {{TOOLS_LIST}} | Available tools | Yes | `- rag\n- file_search` |
| {{ROLE_TITLE}} | Role identity title | No | `Domain Analysis Specialist` |
| {{WORKFLOW_PHASES}} | Agent workflow phases | No | See body template |
| {{TOOL_CALL_BLOCKS}} | Tool invocation examples | No | See spec Section 4 |

## Frontmatter Template

```yaml
---
id: {{AGENT_ID}}
name: {{AGENT_NAME}}
version: {{VERSION}}
category: {{CATEGORY}}
description: |
  {{DESCRIPTION}}
model_id: {{MODEL_ID}}
enabled: true

triggers:
  keywords:
{{KEYWORDS_LIST}}
  phases:
{{PHASES_LIST}}
  complexity_range: [{{COMPLEXITY_MIN}}, {{COMPLEXITY_MAX}}]
  state_conditions: {}
  defect_types: []

capabilities:
{{CAPABILITIES_LIST}}

tools:
{{TOOLS_LIST}}

execution_targets:
  default: cpu
  fallback:
    - gpu

constraints:
  max_file_changes: 10
  max_lines_per_file: 300
  requires_review: true
  timeout_seconds: 300
  max_steps: 50

conversation_starters:
  - "Analyze this task for domain requirements"
  - "What domains are involved in this project"

color: blue

metadata:
  author: {{AUTHOR}}
  created: "{{CREATED_DATE}}"
  tags:
{{TAGS_LIST}}
---
```

## Prompt Body Template

```markdown
# {{AGENT_NAME}} — {{ROLE_TITLE}}

## Identity and Purpose

[Describe the agent's primary role and when it activates. Explain what the agent is responsible for and what it is not responsible for. Include complexity range and phase triggers.]

## Core Principles

[List 3-5 core principles that guide the agent's behavior. These should reflect best practices for the agent's domain.]

- **Principle 1:** Description
- **Principle 2:** Description
- **Principle 3:** Description

## Workflow

### Phase 1: {{PHASE_1_NAME}}

[Describe the first phase of the agent's workflow. Include specific activities and decision points.]

```tool-call
CALL: {{TOOL_NAME}} "{{PARAMETERS}}"
purpose: {{TOOL_PURPOSE}}
capture: {{CAPTURE_VARIABLE}}
```

### Phase 2: {{PHASE_2_NAME}}

[Describe the second phase. Include any conditional logic if needed.]

```tool-call
IF: {{CONDITION}}
CALL: {{TOOL_NAME}} "{{PARAMETERS}}"
purpose: {{TOOL_PURPOSE}}
prompt: |
  [Multi-line prompt if needed]
END IF:
```

### Phase 3: {{PHASE_3_NAME}}

[Describe the final phase and output production.]

## Input Contract

The {{AGENT_NAME}} receives:
- `input_name`: Description of expected input

## Output Contract

The {{AGENT_NAME}} produces:
- `output_name`: Description of produced output

## Output Specification

[Describe the format and structure of the agent's output. Include any templates or schemas the output must conform to.]

## Constraints and Safety

[List constraints the agent must respect and safety considerations.]

- **Constraint 1:** Description
- **Safety 1:** Description

## Quality Criteria

The agent's output is high-quality when:
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Related Components

- [[component-framework/{{RELATED_COMPONENT_PATH}}]] - Related component
```

## Generation Instructions

### Step 1: Gather Agent Requirements

Collect the following information:
1. Agent purpose and scope
2. Trigger conditions (keywords, phases, complexity)
3. Required capabilities
4. Tool requirements
5. Workflow phases

### Step 2: Populate Frontmatter

Fill in all required frontmatter fields:
- Use lowercase hyphenated format for `id`
- Ensure `version` follows semver format
- `complexity_range` must use list format `[min, max]`
- All list items must be indented with 2 spaces

### Step 3: Define Prompt Body

Create the agent's system prompt:
1. Write clear identity and purpose
2. Define 3-5 core principles
3. Specify workflow phases with tool-call blocks
4. Document input/output contracts
5. List quality criteria

### Step 4: Validate Generated Agent

After generation, validate:
```python
# Load and validate the generated agent
loader = ComponentLoader()
errors = loader.validate_component("agents/{{AGENT_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify agent loads in registry
agent = await registry.load_agent("{{AGENT_ID}}")
assert agent is not None
assert agent.system_prompt != ""
```

## Tool-Call Syntax Reference

Follow the syntax defined in agent-ecosystem-design-spec.md Section 4:

### Basic CALL
```tool-call
CALL: tool_name "parameters"
purpose: Why this tool is called
capture: variable_name
```

### MCP Tool Call
```tool-call
CALL: mcp__server_name__tool_name "parameters"
purpose: Why this MCP tool is called
```

### CALL with Prompt
```tool-call
CALL: mcp__clear-thought__sequentialthinking
purpose: Analyze the problem
prompt: |
  Step 1: What is the problem?
  Step 2: What are possible solutions?
```

### Conditional CALL
```tool-call
IF: condition_expression
CALL: tool_name "parameters"
purpose: Why this conditional call
END IF:
```

## Quality Checklist

Before finalizing the agent definition:

- [ ] Frontmatter has all required fields
- [ ] `complexity_range` uses list format `[min, max]`
- [ ] `template_id` matches agent `id`
- [ ] `version` follows semver format (e.g., 1.0.0)
- [ ] Tool-call blocks conform to spec Section 4 syntax
- [ ] System prompt is non-empty and meaningful
- [ ] Input/output contracts are clearly defined
- [ ] Quality criteria are specific and measurable
- [ ] Related components are correctly linked

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/templates/ecosystem-config.md]] - Ecosystem configuration template
- [[component-framework/personas/specialist-agent.md]] - Specialist agent persona
