---
template_id: ecosystem-config
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for configuring agent ecosystem instances
schema_version: "1.0"
---

# Ecosystem Configuration Meta-Template

## Purpose

This meta-template provides the structure for configuring agent ecosystem instances. Use this template when setting up a new ecosystem deployment or configuring an existing ecosystem for a specific use case.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{ECOSYSTEM_ID}} | Unique ecosystem identifier | Yes | `gaia-dev-ecosystem` |
| {{ECOSYSTEM_NAME}} | Human-readable ecosystem name | Yes | `GAIA Development Ecosystem` |
| {{VERSION}} | Configuration version | Yes | `1.0.0` |
| {{TARGET_DOMAIN}} | Primary domain for ecosystem | Yes | `software-development` |
| {{AGENT_LIST}} | List of agents in ecosystem | Yes | See agent config section |
| {{WORKFLOW_PATTERN}} | Primary workflow pattern | Yes | `pipeline-workflow` |
| {{MEMORY_CONFIG}} | Memory configuration | Yes | See memory section |
| {{KNOWLEDGE_CONFIG}} | Knowledge base configuration | Yes | See knowledge section |

## Configuration Structure

```yaml
# Ecosystem Configuration
ecosystem:
  id: {{ECOSYSTEM_ID}}
  name: {{ECOSYSTEM_NAME}}
  version: {{VERSION}}
  target_domain: {{TARGET_DOMAIN}}

# Agent Configuration
agents:
{{AGENT_CONFIGS}}

# Workflow Configuration
workflows:
{{WORKFLOW_CONFIG}}

# Memory Configuration
memory:
{{MEMORY_CONFIG}}

# Knowledge Configuration
knowledge:
{{KNOWLEDGE_CONFIG}}

# Component Configuration
components:
{{COMPONENT_CONFIG}}

# Runtime Configuration
runtime:
{{RUNTIME_CONFIG}}
```

## Agent Configuration Section

```yaml
agents:
  # Core Pipeline Agents
  - id: domain-analyzer
    name: Domain Analyzer
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      max_context_tokens: 8192
      temperature: 0.3
      pipeline_entry_point: true

  - id: workflow-modeler
    name: Workflow Modeler
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      max_context_tokens: 8192
      temperature: 0.3
      requires_input: domain-analyzer

  - id: loom-builder
    name: Loom Builder
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      max_context_tokens: 4096
      temperature: 0.2
      requires_input: workflow-modeler

  - id: ecosystem-builder
    name: Ecosystem Builder
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      max_context_tokens: 8192
      temperature: 0.3
      requires_input: loom-builder

  # Specialist Agents (Add as needed)
  - id: python-developer
    name: Python Developer
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      domain: python-development
      tools: [file_read, file_write, bash_execute, run_tests]

  - id: code-reviewer
    name: Code Reviewer
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      domain: code-quality
      tools: [file_read, grep, run_tests]

  # Coordinator Agents
  - id: pipeline-coordinator
    name: Pipeline Coordinator
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      role: coordination
      manages: [domain-analyzer, workflow-modeler, loom-builder, ecosystem-builder]

  # Validator Agents
  - id: quality-validator
    name: Quality Validator
    version: 1.0.0
    enabled: true
    config:
      model: Qwen3.5-35B-A3B-GGUF
      role: validation
      validates: [agent-outputs, pipeline-artifacts]
```

## Workflow Configuration Section

```yaml
workflows:
  primary_pattern: {{WORKFLOW_PATTERN}}

  pipeline:
    stages:
      - stage_id: 1
        name: Domain Analysis
        agent: domain-analyzer
        artifact: blueprint
        quality_gate: true

      - stage_id: 2
        name: Workflow Modeling
        agent: workflow-modeler
        artifact: workflow_model
        quality_gate: true
        requires: [1]

      - stage_id: 3
        name: Loom Building
        agent: loom-builder
        artifact: pipeline_topology
        quality_gate: true
        requires: [2]

      - stage_id: 4
        name: Ecosystem Construction
        agent: ecosystem-builder
        artifact: ecosystem_manifest
        quality_gate: true
        requires: [3]

    error_handling:
      on_stage_failure: retry
      max_retries: 2
      retry_delay_seconds: 5
      escalation_agent: pipeline-coordinator

  alternative_workflows:
    - pattern: agile-workflow
      enabled: false
      trigger: iterative_development

    - pattern: waterfall-workflow
      enabled: false
      trigger: fixed_requirements
```

## Memory Configuration Section

```yaml
memory:
  short_term:
    enabled: true
    max_turns: 10
    persistence: session

  working:
    enabled: true
    max_entries: 100
    organization: hierarchical

  long_term:
    enabled: true
    storage: vector_database
    indexing: semantic

  episodic:
    enabled: true
    retention_days: 30
    compression: enabled
```

## Knowledge Configuration Section

```yaml
knowledge:
  domain_knowledge:
    sources:
      - type: component_framework
        path: component-framework/knowledge/
        auto_index: true

      - type: external
        name: Domain Documentation
        url: {{DOCUMENTATION_URL}}
        sync_frequency: daily

  procedural_knowledge:
    sources:
      - type: component_framework
        path: component-framework/tasks/
        auto_index: true

      - type: learned
        from_episodes: true
        validation_required: true

  knowledge_graph:
    enabled: true
    ontology:
      - entities: [Agent, Domain, Task, Tool, Component]
      - relationships: [implements, requires, validates, produces]
    auto_update: true
```

## Component Configuration Section

```yaml
components:
  commands:
    load_from:
      - component-framework/commands/
    auto_register: true

  tasks:
    load_from:
      - component-framework/tasks/
    auto_register: true

  checklists:
    load_from:
      - component-framework/checklists/
    validation_required: true

  documents:
    load_from:
      - component-framework/documents/
    templates_enabled: true
```

## Runtime Configuration Section

```yaml
runtime:
  execution:
    default_model: Qwen3-0.6B-GGUF
    fallback_model: Qwen3-0.6B-GGUF
    max_concurrent_agents: 4
    timeout_default_seconds: 300

  logging:
    level: INFO
    format: structured
    output:
      - console
      - file
    file_path: logs/ecosystem.log

  monitoring:
    metrics_enabled: true
    health_check_interval: 60
    alerting:
      enabled: false
      channels: []

  security:
    require_review_for_changes: true
    max_file_changes_per_turn: 10
    allowed_file_patterns:
      - "**/*.py"
      - "**/*.md"
      - "**/*.yaml"
      - "**/*.yml"
    blocked_file_patterns:
      - "**/*.env"
      - "**/credentials/*"
      - "**/.git/*"
```

## Generation Instructions

### Step 1: Define Ecosystem Scope

Identify:
1. Primary domain and use case
2. Required agents for the domain
3. Workflow pattern to use
4. Resource constraints

### Step 2: Configure Agents

For each required agent:
1. Set agent ID and name
2. Configure model and parameters
3. Define tool access
4. Specify dependencies

### Step 3: Configure Workflow

1. Select primary workflow pattern
2. Define stage sequence
3. Set quality gates
4. Configure error handling

### Step 4: Configure Infrastructure

1. Set up memory components
2. Configure knowledge sources
3. Register component loaders
4. Set runtime parameters

### Step 5: Validate Configuration

```python
# Load and validate ecosystem configuration
from gaia.utils.component_loader import ComponentLoader

loader = ComponentLoader()
config = loader.load_component("templates/ecosystem-config.md")

# Validate required sections
required_sections = ["ecosystem", "agents", "workflows", "memory", "knowledge"]
for section in required_sections:
    assert section in config["content"], f"Missing section: {section}"

# Validate agent references
agent_ids = extract_agent_ids(config["content"])
for agent_id in agent_ids:
    agent_file = f"config/agents/{agent_id}.md"
    assert Path(agent_file).exists(), f"Agent not found: {agent_id}"
```

## Quality Checklist

- [ ] All required configuration sections present
- [ ] Agent IDs reference existing agents
- [ ] Workflow stages have valid agent assignments
- [ ] Memory configuration matches use case
- [ ] Knowledge sources are accessible
- [ ] Runtime constraints are reasonable
- [ ] Security settings are appropriate
- [ ] Configuration validates without errors

## Related Components

- [[component-framework/templates/agent-definition.md]] - Agent generation template
- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/workflows/pipeline-workflow.md]] - Pipeline workflow pattern
