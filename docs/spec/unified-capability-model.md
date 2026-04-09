# Unified Capability Model for GAIA Agents

**Version:** 1.0.0
**Date:** 2026-04-08
**Author:** Jordan Blake, Principal Software Engineer & Technical Lead
**Status:** Proposed

---

## Executive Summary

This document proposes a unified capability vocabulary for GAIA agents, addressing the current bifurcation between:
- **MD-format agents:** Explicit `capabilities:` list in YAML frontmatter
- **Python class agents:** Implicit capabilities through tool method names
- **Component templates:** Referenced capabilities without formal definition

The unified model enables:
- Consistent capability discovery across all agent types
- Capability-based agent routing and selection
- Machine-readable capability registries
- Clear capability-to-tool mapping

---

## Current State Analysis

### Capability Vocabulary Bifurcation

**MD-Format Agents** (`agents/domain_analyzer.md`):
```yaml
capabilities:
  - domain-analysis
  - requirements-extraction
  - dependency-mapping
  - keyword-extraction
```

**Python Class Agents** (`src/gaia/pipeline/stages/gap_detector.py`):
```python
def _register_tools(self):
    @tool
    def scan_available_agents(...) -> Dict
    @tool
    def compare_agents(...) -> Dict
    @tool
    def analyze_gaps(...) -> Dict
```
*Capabilities are implicit in tool names*

**Component Templates** (`component-framework/checklists/`):
```yaml
# Referenced but not formally defined
capabilities_required:
  - domain-analysis
  - workflow-modeling
```

### Problems with Current State

1. **Inconsistent naming:** `domain-analysis` vs `analyze_domain` vs `AnalyzeDomain`
2. **No capability registry:** Cannot discover what capabilities exist system-wide
3. **No validation:** No way to verify MD capabilities match Python tools
4. **No capability metadata:** No descriptions, input/output schemas, or complexity scores

---

## Proposed Unified Capability Model

### Capability Definition Schema

```yaml
# Capability Registry Entry
capability:
  id: domain-analysis
  name: Domain Analysis
  description: |
    Analyzes input tasks to identify knowledge domains,
    requirements, and cross-domain dependencies.

  # Categorization
  category: analysis
  subcategory: domain-mapping

  # Input/Output Contracts
  input_schema:
    task: string
    context: object (optional)

  output_schema:
    primary_domain: string
    secondary_domains: array[string]
    domain_requirements: object
    domain_constraints: object
    cross_domain_dependencies: array
    confidence_score: float (0.0-1.0)

  # Implementation Mapping
  implementations:
    - type: python
      entrypoint: "src/gaia/pipeline/stages/domain_analyzer.py::DomainAnalyzer.analyze"
      tool_name: analyze_domain

    - type: prompt
      template: "agents/domain_analyzer.md"

  # Quality Attributes
  complexity_range: [0.3, 0.8]
  execution_time_estimate: "30-60 seconds"

  # Dependencies
  requires_capabilities: []
  provides_artifacts: ["domain_blueprint"]

  # Metadata
  version: 1.0.0
  created: 2026-04-07
  maintainer: GAIA Team
```

### Unified Capability Vocabulary

The following capabilities are defined for Phase 5 pipeline agents:

#### Analysis Capabilities
| ID | Name | Description | Python Tool |
|----|------|-------------|-------------|
| `domain-analysis` | Domain Analysis | Identify knowledge domains in tasks | `analyze_domain` |
| `requirements-extraction` | Requirements Extraction | Extract functional/non-functional requirements | `extract_requirements` |
| `dependency-mapping` | Dependency Mapping | Map cross-domain dependencies | `map_dependencies` |
| `gap-analysis` | Gap Analysis | Identify missing agents/capabilities | `analyze_gaps` |
| `complexity-estimation` | Complexity Estimation | Estimate task complexity | `estimate_complexity` |

#### Design Capabilities
| ID | Name | Description | Python Tool |
|----|------|-------------|-------------|
| `workflow-modeling` | Workflow Modeling | Design execution workflows | `model_workflow` |
| `topology-design` | Topology Design | Design agent execution graphs | `build_execution_graph` |
| `agent-selection` | Agent Selection | Select agents for phases | `select_agents_for_phase` |
| `agent-configuration` | Agent Configuration | Configure agents for tasks | `configure_agent` |

#### Orchestration Capabilities
| ID | Name | Description | Python Tool |
|----|------|-------------|-------------|
| `pipeline-orchestration` | Pipeline Orchestration | Coordinate multi-stage execution | `execute_full_pipeline` |
| `agent-spawning` | Agent Spawning | Generate missing agents | `trigger_agent_spawn` |
| `component-binding` | Component Binding | Bind templates to agents | `bind_components` |
| `loom-building` | Loom Building | Construct execution topologies | `build_loom` |

#### Execution Capabilities
| ID | Name | Description | Python Tool |
|----|------|-------------|-------------|
| `pipeline-execution` | Pipeline Execution | Execute agent sequences | `execute_pipeline` |
| `artifact-production` | Artifact Production | Generate output artifacts | `produce_artifact` |
| `quality-validation` | Quality Validation | Validate output quality | `validate_quality` |

#### Generation Capabilities
| ID | Name | Description | Python Tool |
|----|------|-------------|-------------|
| `ecosystem-orchestration` | Ecosystem Orchestration | Coordinate agent ecosystem generation | `orchestrate_ecosystem` |
| `component-generation` | Component Generation | Generate component templates | `generate_component` |
| `agent-creation` | Agent Creation | Create agent definitions | `create_agent` |
| `template-population` | Template Population | Populate templates with content | `populate_template` |

---

## Implementation Architecture

### Capability Registry

```python
# src/gaia/agents/base/capabilities.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class CapabilityCategory(Enum):
    ANALYSIS = "analysis"
    DESIGN = "design"
    ORCHESTRATION = "orchestration"
    EXECUTION = "execution"
    GENERATION = "generation"
    VALIDATION = "validation"


@dataclass
class CapabilitySchema:
    """Schema definition for capability inputs/outputs."""
    fields: Dict[str, str]
    required: List[str]
    optional: List[str] = field(default_factory=list)


@dataclass
class CapabilityDefinition:
    """Definition of a single capability."""
    id: str
    name: str
    description: str
    category: CapabilityCategory
    subcategory: Optional[str] = None

    # Contracts
    input_schema: Optional[CapabilitySchema] = None
    output_schema: Optional[CapabilitySchema] = None

    # Implementation
    python_entrypoint: Optional[str] = None
    tool_name: Optional[str] = None
    md_template: Optional[str] = None

    # Quality
    complexity_range: tuple = (0.0, 1.0)
    execution_time_estimate: Optional[str] = None

    # Dependencies
    requires_capabilities: List[str] = field(default_factory=list)
    provides_artifacts: List[str] = field(default_factory=list)

    # Metadata
    version: str = "1.0.0"


class CapabilityRegistry:
    """Central registry for all capabilities."""

    def __init__(self):
        self._capabilities: Dict[str, CapabilityDefinition] = {}

    def register(self, capability: CapabilityDefinition) -> None:
        """Register a capability definition."""
        self._capabilities[capability.id] = capability

    def get(self, capability_id: str) -> Optional[CapabilityDefinition]:
        """Get capability by ID."""
        return self._capabilities.get(capability_id)

    def get_by_category(self, category: CapabilityCategory) -> List[CapabilityDefinition]:
        """Get all capabilities in a category."""
        return [
            cap for cap in self._capabilities.values()
            if cap.category == category
        ]

    def get_by_tool_name(self, tool_name: str) -> Optional[CapabilityDefinition]:
        """Find capability by Python tool name."""
        for cap in self._capabilities.values():
            if cap.tool_name == tool_name:
                return cap
        return None

    def validate_agent_capabilities(
        self,
        agent_md_path: str,
        agent_python_class: str
    ) -> List[str]:
        """
        Validate that MD capabilities match Python tools.

        Returns list of validation errors (empty if valid).
        """
        errors = []

        # Load MD capabilities
        md_capabilities = self._load_md_capabilities(agent_md_path)

        # Get Python tools
        python_tools = self._get_python_tools(agent_python_class)

        # Check for mismatches
        for cap_id in md_capabilities:
            cap = self.get(cap_id)
            if not cap:
                errors.append(f"Unknown capability: {cap_id}")
            elif cap.tool_name not in python_tools:
                errors.append(
                    f"Capability {cap_id} (tool: {cap.tool_name}) "
                    f"not found in Python tools: {python_tools}"
                )

        return errors
```

### Agent Frontmatter Integration

```yaml
# agents/domain_analyzer.md
---
id: domain-analyzer
name: Domain Analyzer
version: 1.0.0
category: analysis

# Explicit capabilities with validation against registry
capabilities:
  - id: domain-analysis
    tool: analyze_domain
  - id: requirements-extraction
    tool: extract_requirements
  - id: dependency-mapping
    tool: map_dependencies

# Python implementation entrypoint
pipeline:
  entrypoint: "src/gaia/pipeline/stages/domain_analyzer.py::DomainAnalyzer"

# Trigger configuration
triggers:
  keywords: [analyze, domain, requirements]
  phases: [DOMAIN_ANALYSIS]

# Capability requirements (what this agent needs)
requires_capabilities: []

# Capability outputs (what this agent provides)
provides_capabilities:
  - domain-analysis
  - requirements-extraction
  - dependency-mapping
---
```

### Validation Tool

```python
# src/gaia/utils/capability_validator.py
import yaml
from pathlib import Path
from gaia.agents.base.capabilities import CapabilityRegistry


class CapabilityValidator:
    """Validates capability declarations across agents."""

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def validate_agent_file(self, agent_path: str) -> Dict[str, Any]:
        """Validate an MD agent file's capabilities."""
        path = Path(agent_path)
        content = path.read_text()

        # Parse frontmatter
        parts = content.split("---", 2)
        frontmatter = yaml.safe_load(parts[1])

        errors = []
        warnings = []

        # Validate capabilities
        capabilities = frontmatter.get("capabilities", [])
        for cap in capabilities:
            cap_id = cap.get("id") if isinstance(cap, dict) else cap
            cap_def = self.registry.get(cap_id)

            if not cap_def:
                errors.append(f"Unknown capability: {cap_id}")
            else:
                # Check tool name matches
                if isinstance(cap, dict) and cap.get("tool"):
                    if cap["tool"] != cap_def.tool_name:
                        warnings.append(
                            f"Tool mismatch for {cap_id}: "
                            f"declared '{cap['tool']}' vs registry '{cap_def.tool_name}'"
                        )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "capabilities_found": len(capabilities)
        }
```

---

## Migration Path

### Phase 1: Registry Creation
1. Create `src/gaia/agents/base/capabilities.py` with registry classes
2. Define all Phase 5 capabilities in code
3. Add `CapabilityRegistry` singleton

### Phase 2: MD Frontmatter Updates
1. Update all `agents/*.md` files with structured capabilities:
   ```yaml
   capabilities:
     - id: domain-analysis
       tool: analyze_domain
   ```
2. Add `requires_capabilities` and `provides_capabilities` fields

### Phase 3: Python Tool Alignment
1. Audit all `@tool` decorated methods in pipeline stages
2. Ensure tool names match capability registry
3. Add capability metadata to tool decorators

### Phase 4: Validation Integration
1. Add capability validation to CI/CD pipeline
2. Create `gaia validate capabilities` CLI command
3. Block merges with capability mismatches

### Phase 5: Runtime Discovery
1. Implement capability-based agent routing
2. Add `get_agents_by_capability()` API
3. Enable dynamic agent selection based on required capabilities

---

## Benefits

### For Developers
- **Clear contracts:** Input/output schemas for each capability
- **Type safety:** Validate capabilities at development time
- **Discovery:** Find agents by required capabilities

### For Runtime
- **Dynamic routing:** Select agents based on capability needs
- **Validation:** Verify capability availability before execution
- **Fallback:** Alternative agents with same capabilities

### For Claude Code Integration
- **Declarative config:** MD frontmatter defines capabilities
- **Tool mapping:** Clear link between capabilities and tools
- **Trigger activation:** Phase-based triggering via capabilities

---

## References

- ADR-001: Python Classes vs MD-Format for Phase 5 Pipeline Agents
- Component Framework Design Spec: `docs/spec/component-framework-design-spec.md`
- Agent Base Class: `src/gaia/agents/base/agent.py`
- Gap Detector: `src/gaia/pipeline/stages/gap_detector.py`
