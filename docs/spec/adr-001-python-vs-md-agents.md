# ADR-001: Python Classes vs MD-Format for Phase 5 Pipeline Agents

**Date:** 2026-04-08
**Status:** Proposed
**Author:** Jordan Blake, Principal Software Engineer & Technical Lead
**Deciders:** Technical Architecture Team
**Branch:** feature/pipeline-orchestration-v1

---

## Context

Phase 5 of the pipeline orchestration introduces auto-spawn capable agents that can detect gaps and generate missing agents via the Master Ecosystem Creator. A design fork has been identified:

- **Approach A:** Python class agents (current pattern for pipeline stages)
- **Approach B:** MD-format agents (current pattern in `agents/` directory)

This ADR analyzes both approaches and provides a recommendation for Phase 5 agent implementations.

### Current State

**Python Class Agents** (Pipeline Stages):
```python
# src/gaia/pipeline/stages/domain_analyzer.py
class DomainAnalyzer(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _register_tools(self):
        @tool
        def analyze_domain(task: str) -> Dict:
            # Implementation
```

**MD-Format Agents** (Agent Definitions):
```yaml
# agents/domain_analyzer.md
---
id: domain-analyzer
capabilities:
  - domain-analysis
  - requirements-extraction
pipeline:
  entrypoint: "src/gaia/pipeline/stages/domain_analyzer.py::DomainAnalyzer"
---
```

## Decision Options

### Option A: Python Classes Only

**Description:** All Phase 5 agents remain as Python classes extending `Agent` base class.

**Pros:**
- Full programmatic control over behavior
- Type safety and IDE support
- Direct access to GAIA SDK and tools
- Clear state management via class attributes
- Testable with standard pytest patterns

**Cons:**
- Requires code changes for any behavior modification
- Not directly loadable by Claude Code
- Harder to swap agents at runtime
- No declarative configuration

### Option B: MD-Format Only

**Description:** Migrate all Phase 5 agents to MD-format with YAML frontmatter.

**Pros:**
- Declarative configuration
- Claude Code compatible
- Easy to modify without code changes
- Runtime agent swapping possible
- Human-readable documentation

**Cons:**
- Limited to prompt-based behavior
- Complex logic requires Python entrypoints anyway
- Tool registration still needs Python
- Testing requires loading infrastructure

### Option C: Hybrid Approach (Recommended)

**Description:** Use BOTH formats with clear separation of concerns:
- **Python classes** for executable behavior (pipeline stages, tool implementations)
- **MD-format** for declarative configuration (agent metadata, capability definitions, Claude Code integration)

**Mapping:** MD frontmatter references Python entrypoint via `pipeline.entrypoint` field.

## Decision Outcome

**Selected: Option C - Hybrid Approach**

### Rationale

The analysis reveals that Python classes and MD-format serve **complementary purposes**, not competing ones:

| Concern | Python Class | MD-Format |
|---------|-------------|-----------|
| **Executable Logic** | Yes (methods, tools) | No (prompts only) |
| **Declarative Config** | No (code-based) | Yes (YAML frontmatter) |
| **Claude Code Integration** | No | Yes (native format) |
| **Runtime Discovery** | Via reflection | Via file parsing |
| **Capability Definition** | Implicit (tools) | Explicit (frontmatter list) |
| **Trigger Configuration** | Hardcoded | Declarative (keywords, phases) |

### Architecture Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Definition                          │
│                     (MD-Format)                              │
├─────────────────────────────────────────────────────────────┤
│  id: domain-analyzer                                         │
│  capabilities: [domain-analysis, requirements-extraction]   │
│  pipeline:                                                   │
│    entrypoint: "src/gaia/pipeline/stages/domain_analyzer.py::DomainAnalyzer"
│  triggers:                                                   │
│    keywords: [analyze, domain, requirements]                │
│    phases: [DOMAIN_ANALYSIS]                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ entrypoint reference
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Implementation                      │
│                    (Python Class)                            │
├─────────────────────────────────────────────────────────────┤
│  class DomainAnalyzer(Agent):                               │
│      def _register_tools(self):                             │
│          @tool                                              │
│          def analyze_domain(...) -> Dict                    │
│      def analyze(self, task: str) -> Dict                   │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Guidelines

### For Phase 5 Agents

1. **Create Python class** in `src/gaia/pipeline/stages/` for any new pipeline stage
2. **Create MD definition** in `agents/` directory with:
   - Complete frontmatter metadata
   - `pipeline.entrypoint` referencing the Python class
   - `capabilities` list matching tool names
   - `triggers` for keyword/phase activation

3. **Register capabilities** in both:
   - Python: Tool names should match capability names
   - MD: List capabilities explicitly in frontmatter

4. **Use ComponentLoader** to load MD definitions for:
   - Runtime agent discovery
   - Capability-based routing
   - Claude Code integration

### Code Example

```python
# src/gaia/pipeline/stages/gap_detector.py
class GapDetector(Agent):
    def _register_tools(self):
        @tool
        def scan_available_agents(...) -> Dict
        @tool
        def compare_agents(...) -> Dict
        @tool
        def analyze_gaps(...) -> Dict
```

```yaml
# agents/gap-detector.md
---
id: gap-detector
capabilities:
  - scan-available-agents
  - compare-agents
  - analyze-gaps
pipeline:
  entrypoint: "src/gaia/pipeline/stages/gap_detector.py::GapDetector"
triggers:
  keywords: [gap, missing-agents, detect]
  phases: [GAP_DETECTION]
---
```

## Consequences

### Positive
- **Best of both worlds:** Leverages strengths of both formats
- **Claude Code compatible:** MD definitions work with Claude Code agents
- **Maintainable:** Clear separation between config and implementation
- **Discoverable:** Capabilities explicitly declared for runtime routing
- **Testable:** Python classes remain unit-testable

### Negative
- **Dual maintenance:** Changes may require updating both files
- **Potential drift:** Risk of MD and Python getting out of sync
- **Additional complexity:** Need loader infrastructure

### Mitigation Strategies

1. **Automated validation:** Add lint check to verify MD capabilities match Python tool names
2. **Single source of truth:** Python class is authoritative; MD is declarative layer
3. **Code generation:** Consider generating MD from Python docstrings

## Compliance

All Phase 5 agents must:
- [ ] Have Python class implementation in `src/gaia/pipeline/stages/`
- [ ] Have MD definition in `agents/` directory
- [ ] Include `pipeline.entrypoint` in MD frontmatter
- [ ] List capabilities in MD frontmatter
- [ ] Register tools matching capability names

## References

- Component Framework Design Spec: `docs/spec/component-framework-design-spec.md`
- Agent Base Class: `src/gaia/agents/base/agent.py`
- Component Loader: `src/gaia/utils/component_loader.py`
- Gap Detector: `src/gaia/pipeline/stages/gap_detector.py`
- Domain Analyzer MD: `agents/domain_analyzer.md`
