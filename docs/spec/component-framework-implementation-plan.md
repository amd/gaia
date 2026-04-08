# Component Framework Implementation Plan

**Version:** 1.0.0
**Status:** READY FOR EXECUTION
**Prepared by:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Date:** 2026-04-07
**Branch:** feature/pipeline-orchestration-v1
**Assignee:** senior-developer

---

## Executive Summary

This document provides the implementation plan for the Component Framework - a top-level directory (`component-framework/`) containing reusable templates and patterns that agents actively reference during pipeline execution.

### Deliverables Overview

| Deliverable | Description | Priority |
|-------------|-------------|----------|
| 1. Directory Structure | Create `component-framework/` with 6 subdirectories | P0 |
| 2. Template Files | Populate 24 template files (4 per subdirectory) | P0 |
| 3. Component Loader | Implement `src/gaia/utils/component_loader.py` | P0 |
| 4. Agent Integration | Update agent definitions to reference framework | P1 |
| 5. Pipeline Integration | Wire framework into pipeline stages | P0 |
| 6. Documentation | Update pipeline docs with framework usage | P1 |

---

## Revised Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MULTI-STAGE PIPELINE                               │
│                     (Pipeline Engine Orchestrates)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐          │
│  │   STAGE 1       │    │   STAGE 2       │    │   STAGE 3       │          │
│  │   Domain        │───>│   Workflow      │───>│   Loom          │          │
│  │   Analyzer      │    │   Modeler       │    │   Builder       │          │
│  │                 │    │                 │    │                 │          │
│  │  AGENT:         │    │  AGENT:         │    │  AGENT:         │          │
│  │  domain-        │    │  workflow-      │    │  loom-          │          │
│  │  analyzer       │    │  modeler        │    │  builder        │          │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘          │
│           │                      │                      │                   │
│           │ READS:               │ READS:               │ READS:            │
│           │ - checklists/        │ - knowledge/         │ - tasks/          │
│           │   domain-analysis    │   domain-knowledge   │   task-breakdown  │
│           │ - memory/working     │ - memory/long-term   │ - documents/      │
│           │                      │   episodic-memory    │                   │
│           │ WRITES:              │ WRITES:              │ WRITES:           │
│           │ - knowledge/         │ - tasks/             │ - commands/       │
│           │   domain-knowledge   │   workflow-model     │   pipeline-cmds   │
│           │ - memory/            │ - memory/            │ - memory/         │
│           │   episodic           │   working            │   working         │
│           │                      │                      │                   │
│           └──────────────────────┴──────────────────────┘                   │
│                                      │                                       │
│                                      v                                       │
│                    ┌─────────────────────────────────┐                       │
│                    │    component-framework/          │                       │
│                    │  (Top-level project directory)   │                       │
│                    │                                  │                       │
│                    │  ├── memory/                     │                       │
│                    │  │   ├── short-term-memory.md    │                       │
│                    │  │   ├── long-term-memory.md     │                       │
│                    │  │   ├── working-memory.md       │                       │
│                    │  │   └── episodic-memory.md      │                       │
│                    │  ├── knowledge/                  │                       │
│                    │  │   ├── domain-knowledge.md     │                       │
│                    │  │   ├── procedural-knowledge.md │                       │
│                    │  │   ├── declarative-knowledge.md│                       │
│                    │  │   └── knowledge-graph.md      │                       │
│                    │  ├── tasks/                      │                       │
│                    │  │   ├── task-breakdown.md       │                       │
│                    │  │   ├── task-dependency.md      │                       │
│                    │  │   ├── task-priority.md        │                       │
│                    │  │   └── task-tracking.md        │                       │
│                    │  ├── commands/                   │                       │
│                    │  │   ├── shell-commands.md       │                       │
│                    │  │   ├── git-commands.md         │                       │
│                    │  │   ├── build-commands.md       │                       │
│                    │  │   └── test-commands.md        │                       │
│                    │  ├── documents/                  │                       │
│                    │  │   ├── design-doc.md           │                       │
│                    │  │   ├── api-spec.md             │                       │
│                    │  │   ├── meeting-notes.md        │                       │
│                    │  │   └── status-report.md        │                       │
│                    │  └── checklists/                 │                       │
│                    │      ├── domain-analysis-...     │                       │
│                    │      ├── workflow-modeling-...   │                       │
│                    │      ├── code-review-...         │                       │
│                    │      └── deployment-...          │                       │
│                    └─────────────────────────────────┘                       │
│                                      │                                       │
│                                      v                                       │
│                    ┌─────────────────────────────────┐                       │
│                    │    src/gaia/utils/              │                       │
│                    │    component_loader.py          │                       │
│                    │                                  │                       │
│                    │    - load_component()           │                       │
│                    │    - render_component()         │                       │
│                    │    - list_components()          │                       │
│                    │    - validate_component()       │                       │
│                    └─────────────────────────────────┘                       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

                              AGENT DEFINITIONS
                              (config/agents/*.yaml / *.md)
                              ┌─────────────────────────┐
                              │  domain-analyzer.yaml   │
                              │  workflow-modeler.yaml  │
                              │  loom-builder.yaml      │
                              │  ...                    │
                              │                         │
                              │  Tools Reference:       │
                              │  - load_component       │
                              │  - save_component       │
                              │  - update_component     │
                              └─────────────────────────┘
                                       │
                                       v
                              Component Framework
                              (Agents read/write during
                               pipeline execution)
```

---

## Implementation Tasks

### Phase 1: Directory Structure (P0)

**Task 1.1: Create component-framework/ directory**

```bash
# Create top-level directory
mkdir component-framework

# Create subdirectories
mkdir -p component-framework/memory
mkdir -p component-framework/knowledge
mkdir -p component-framework/tasks
mkdir -p component-framework/commands
mkdir -p component-framework/documents
mkdir -p component-framework/checklists
```

**Task 1.2: Add .gitignore entries**

Add to project `.gitignore`:
```
# Component Framework runtime files
component-framework/**/*.lock
component-framework/**/.lock
component-framework/memory/working-memory.md.lock
```

**Exit Criteria:**
- [ ] All 7 directories exist
- [ ] .gitignore updated
- [ ] Directory structure committed to git

---

### Phase 2: Template Population (P0)

**Task 2.1: Memory Templates (4 files)**

Create the following files in `component-framework/memory/`:

| File | Template ID | Description |
|------|-------------|-------------|
| `short-term-memory.md` | short-term-memory | Current turn context |
| `long-term-memory.md` | long-term-memory | Persistent knowledge |
| `working-memory.md` | working-memory | Active scratchpad |
| `episodic-memory.md` | episodic-memory | Execution history |

**Template Format:**
```markdown
---
template_id: <id>
template_type: memory
version: 1.0.0
created: "2026-04-07"
description: <one-line purpose>
---

# Template Title

## Purpose

[Purpose statement]

## Structure

[Template sections]

## Usage Protocol

[How to use]
```

**Exit Criteria:**
- [ ] All 4 memory template files created
- [ ] All files have valid YAML frontmatter
- [ ] All files validated with `gaia component-framework validate`

**Task 2.2: Knowledge Templates (4 files)**

Create in `component-framework/knowledge/`:

| File | Template ID |
|------|-------------|
| `domain-knowledge.md` | domain-knowledge |
| `procedural-knowledge.md` | procedural-knowledge |
| `declarative-knowledge.md` | declarative-knowledge |
| `knowledge-graph.md` | knowledge-graph |

**Task 2.3: Task Templates (4 files)**

Create in `component-framework/tasks/`:

| File | Template ID |
|------|-------------|
| `task-breakdown.md` | task-breakdown |
| `task-dependency.md` | task-dependency |
| `task-priority.md` | task-priority |
| `task-tracking.md` | task-tracking |

**Task 2.4: Command Templates (4 files)**

Create in `component-framework/commands/`:

| File | Template ID |
|------|-------------|
| `shell-commands.md` | shell-commands |
| `git-commands.md` | git-commands |
| `build-commands.md` | build-commands |
| `test-commands.md` | test-commands |

**Task 2.5: Document Templates (4 files)**

Create in `component-framework/documents/`:

| File | Template ID |
|------|-------------|
| `design-doc.md` | design-doc |
| `api-spec.md` | api-spec |
| `meeting-notes.md` | meeting-notes |
| `status-report.md` | status-report |

**Task 2.6: Checklist Templates (4 files)**

Create in `component-framework/checklists/`:

| File | Template ID |
|------|-------------|
| `domain-analysis-checklist.md` | domain-analysis-checklist |
| `workflow-modeling-checklist.md` | workflow-modeling-checklist |
| `code-review-checklist.md` | code-review-checklist |
| `deployment-checklist.md` | deployment-checklist |

**Exit Criteria for Phase 2:**
- [ ] All 24 template files created
- [ ] All files pass `gaia component-framework validate`
- [ ] Templates committed to git

---

### Phase 3: Component Loader Utility (P0)

**Task 3.1: Implement ComponentLoader class**

Create `src/gaia/utils/component_loader.py`:

```python
"""
GAIA Component Loader

Load and manage Component Framework templates.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


class ComponentLoader:
    """Component Framework template loader."""

    def __init__(self, framework_dir: Optional[Path] = None):
        self._framework_dir = framework_dir or Path("component-framework")
        self._loaded_components: Dict[str, Any] = {}

    def load_component(self, component_path: str) -> Dict[str, Any]:
        """Load a component template."""
        # Implementation per design spec Section 4.2

    def render_component(
        self,
        component_path: str,
        variables: Dict[str, str],
    ) -> str:
        """Render template with variable substitution."""

    def list_components(self, component_type: Optional[str] = None) -> List[str]:
        """List available components."""

    def validate_component(self, component_path: str) -> List[str]:
        """Validate a component template."""
```

**Task 3.2: Export ComponentLoader**

Update `src/gaia/utils/__init__.py`:
```python
from gaia.utils.component_loader import ComponentLoader

__all__ = [
    # ... existing exports
    "ComponentLoader",
]
```

**Task 3.3: Unit Tests**

Create `tests/unit/utils/test_component_loader.py`:
```python
"""Unit tests for ComponentLoader."""

import pytest
from gaia.utils.component_loader import ComponentLoader


class TestComponentLoader:
    """Test ComponentLoader functionality."""

    def test_load_component_success(self, tmp_path):
        """Test loading a valid component."""

    def test_load_component_missing(self):
        """Test loading non-existent component raises FileNotFoundError."""

    def test_load_component_no_frontmatter(self, tmp_path):
        """Test loading component without frontmatter raises ValueError."""

    def test_render_component_variables(self, tmp_path):
        """Test variable substitution in template."""

    def test_list_components(self, tmp_path):
        """Test listing available components."""

    def test_validate_component_valid(self, tmp_path):
        """Test validation of valid component."""

    def test_validate_component_missing_fields(self, tmp_path):
        """Test validation catches missing required fields."""
```

**Exit Criteria:**
- [ ] ComponentLoader implemented
- [ ] All 7 unit tests passing
- [ ] ComponentLoader exported in utils/__init__.py

---

### Phase 4: Agent Integration (P1)

**Task 4.1: Add Component Tools to Agent Base Class**

Update `src/gaia/agents/base/agent.py`:

```python
class Agent(abc.ABC):
    # Add component framework tools

    @tool
    async def load_component(self, component_path: str) -> Dict[str, Any]:
        """Load a component template from the framework."""
        loader = ComponentLoader()
        return loader.load_component(component_path)

    @tool
    async def save_component(
        self,
        component_path: str,
        content: str,
        frontmatter: Dict[str, Any],
    ) -> str:
        """Save a component to the framework."""

    @tool
    async def update_component(
        self,
        component_path: str,
        section: str,
        content: str,
    ) -> str:
        """Update a specific section of a component."""

    @tool
    async def list_components(
        self,
        component_type: Optional[str] = None,
    ) -> List[str]:
        """List available components."""
```

**Task 4.2: Update Agent YAML Definitions**

Update agent definitions in `config/agents/` to include component tools:

```yaml
# Example: config/agents/domain-analyzer.yaml
tools:
  - file_read
  - file_write
  - load_component      # NEW
  - save_component      # NEW
  - update_component    # NEW
  - list_components     # NEW
```

**Task 4.3: Update Agent Prompts**

Add to agent system prompts:

```markdown
## Component Framework Usage

You have access to the Component Framework at `component-framework/`:

1. Before starting: Load relevant checklists
2. During work: Update working-memory.md
3. After completion: Write to knowledge/ and episodic-memory.md
```

**Exit Criteria:**
- [ ] Component tools added to Agent base class
- [ ] Pipeline stage agents updated with new tools
- [ ] Agent prompts reference component-framework

---

### Phase 5: Pipeline Integration (P0)

**Task 5.1: Update Pipeline Stages**

Update pipeline engine to initialize component framework:

```python
# src/gaia/pipeline/engine.py
from gaia.utils.component_loader import ComponentLoader

class PipelineEngine:
    async def initialize(self, context: PipelineContext, config: PipelineConfig):
        # ... existing initialization
        self.component_loader = ComponentLoader()
        await self._initialize_component_framework()
```

**Task 5.2: Add Component Framework Paths**

Update pipeline configuration to include framework paths:

```python
@dataclass
class PipelineConfig:
    # ... existing fields
    component_framework_dir: Optional[str] = None  # NEW
```

**Task 5.3: Document Pipeline Integration**

Update pipeline documentation to reference component-framework usage.

**Exit Criteria:**
- [ ] Pipeline engine initializes ComponentLoader
- [ ] Pipeline stages read/write component templates
- [ ] Pipeline documentation updated

---

## Acceptance Criteria

### Functional Requirements

| ID | Requirement | Verification Method |
|----|-------------|---------------------|
| F1 | `component-framework/` directory exists at project root | Directory check |
| F2 | All 6 subdirectories created (memory, knowledge, tasks, commands, documents, checklists) | Directory listing |
| F3 | All 24 template files have valid YAML frontmatter | Validation script |
| F4 | ComponentLoader can load all templates | Unit tests |
| F5 | ComponentLoader can render templates with variable substitution | Unit tests |
| F6 | Agents can access component tools (load, save, update, list) | Integration test |
| F7 | Pipeline stages successfully read/write components | End-to-end test |

### Non-Functional Requirements

| ID | Requirement | Verification Method |
|----|-------------|---------------------|
| NF1 | Templates load in < 100ms | Performance test |
| NF2 | Component framework does not block pipeline execution | Concurrency test |
| NF3 | All templates are human-readable Markdown | Manual review |
| NF4 | Variable substitution handles all `{{VARIABLE}}` patterns | Unit tests |

---

## Testing Strategy

### Unit Tests

| Test File | Coverage |
|-----------|----------|
| `tests/unit/utils/test_component_loader.py` | ComponentLoader class |
| `tests/unit/agents/test_component_tools.py` | Agent component tools |

### Integration Tests

| Test File | Coverage |
|-----------|----------|
| `tests/integration/test_component_framework.py` | End-to-end framework usage |
| `tests/integration/test_pipeline_components.py` | Pipeline stage integration |

### Validation Script

Create `scripts/validate_component_framework.py`:
```python
#!/usr/bin/env python3
"""Validate all component framework templates."""

from pathlib import Path
from gaia.utils.component_loader import ComponentLoader


def main():
    loader = ComponentLoader()
    errors = []

    for component in loader.list_components():
        validation_errors = loader.validate_component(component)
        if validation_errors:
            errors.extend(
                f"{component}: {error}"
                for error in validation_errors
            )

    if errors:
        print("Validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    else:
        print("All templates validated successfully!")
        return 0


if __name__ == "__main__":
    exit(main())
```

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| R1: Template files created with invalid frontmatter | Medium | High | Validation script runs before commit |
| R2: ComponentLoader breaks existing agent functionality | Low | High | Unit tests verify backward compatibility |
| R3: Pipeline stages fail to find component-framework/ | Medium | Medium | Use absolute paths, add error handling |
| R4: Variable substitution fails for edge cases | Medium | Low | Comprehensive unit test coverage |
| R5: Templates become stale as agent definitions evolve | High | Medium | Document maintenance responsibility |

---

## Implementation Timeline

### Iteration 1 (Current)
- [x] Design spec completed
- [ ] Phase 1: Directory structure
- [ ] Phase 2: Template population (24 files)
- [ ] Phase 3: ComponentLoader implementation

### Iteration 2 (Next)
- [ ] Phase 4: Agent integration
- [ ] Phase 5: Pipeline integration
- [ ] Documentation updates

---

## Handoff Notes

### For senior-developer

**Your deliverables:**

1. **Phase 1:** Create directory structure (7 directories total)
2. **Phase 2:** Create all 24 template files per design spec
   - Use exact format from component-framework-design-spec.md Sections 3.1-3.6
   - All files must have valid YAML frontmatter
3. **Phase 3:** Implement ComponentLoader in `src/gaia/utils/component_loader.py`
   - Follow pseudocode in design spec Section 4.2
   - Implement all 4 public methods
4. **Write unit tests:** 7 tests minimum in `tests/unit/utils/test_component_loader.py`
5. **Validation script:** `scripts/validate_component_framework.py`

**Critical notes:**
- Templates must use `{{UPPER_SNAKE_CASE}}` variable convention
- All templates start with `---` YAML frontmatter delimiter
- File encoding must be UTF-8 (use `utf-8-sig` for Windows compatibility)
- Component framework is a TOP-LEVEL directory, NOT hidden (not `.component-framework/`)

**Priority order:**
1. P0: Directory structure + memory templates + ComponentLoader
2. P0: knowledge templates + checklists templates
3. P0: tasks templates + unit tests
4. P1: commands templates + documents templates
5. P1: Agent integration + pipeline integration

---

### For quality-reviewer

**Your deliverables:**

1. **Verify directory structure:** All 7 directories exist
2. **Validate all 24 templates:** Run `scripts/validate_component_framework.py`
3. **Run unit tests:** All 7+ tests must pass
4. **Verify ComponentLoader:**
   - Can load all templates
   - Variable substitution works correctly
   - Error handling for missing/invalid files
5. **Integration test:** Verify agents can access component tools
6. **Pipeline test:** Verify pipeline stages can read/write components

**Critical validation points:**
- All frontmatter has required fields: `template_id`, `template_type`, `version`, `description`
- `template_type` values are one of: memory, knowledge, tasks, commands, documents, checklists
- No syntax errors in YAML frontmatter
- Markdown body is well-formed

---

### For software-program-manager

**Your deliverables:**

1. **Track implementation:** Create work items for each phase
2. **Coordinate handoffs:** Ensure quality-reviewer validates each phase before next begins
3. **Update documentation:** Add component-framework references to:
   - Pipeline documentation
   - Agent developer guide
   - README.md
4. **Plan Phase 2:** Master Ecosystem Creator integration (future spec)

**Dependencies to track:**
- Phase 1 blocked on: Nothing
- Phase 2 blocked on: Phase 1 completion
- Phase 3 blocked on: Phase 2 completion (templates must exist to test loader)
- Phase 4 blocked on: Phase 3 completion (tools depend on ComponentLoader)
- Phase 5 blocked on: Phase 4 completion (pipeline integration needs agent tools)

---

## Appendix A: Template File Checklist

### memory/ (4 files)
- [ ] `short-term-memory.md`
- [ ] `long-term-memory.md`
- [ ] `working-memory.md`
- [ ] `episodic-memory.md`

### knowledge/ (4 files)
- [ ] `domain-knowledge.md`
- [ ] `procedural-knowledge.md`
- [ ] `declarative-knowledge.md`
- [ ] `knowledge-graph.md`

### tasks/ (4 files)
- [ ] `task-breakdown.md`
- [ ] `task-dependency.md`
- [ ] `task-priority.md`
- [ ] `task-tracking.md`

### commands/ (4 files)
- [ ] `shell-commands.md`
- [ ] `git-commands.md`
- [ ] `build-commands.md`
- [ ] `test-commands.md`

### documents/ (4 files)
- [ ] `design-doc.md`
- [ ] `api-spec.md`
- [ ] `meeting-notes.md`
- [ ] `status-report.md`

### checklists/ (4 files)
- [ ] `domain-analysis-checklist.md`
- [ ] `workflow-modeling-checklist.md`
- [ ] `code-review-checklist.md`
- [ ] `deployment-checklist.md`

**Total: 24 template files**

---

## Appendix B: ComponentLoader API Reference

### Class: ComponentLoader

#### `__init__(framework_dir: Optional[Path] = None)`

Initialize component loader.

**Parameters:**
- `framework_dir`: Path to component-framework/ directory. Defaults to `Path("component-framework")`.

#### `load_component(component_path: str) -> Dict[str, Any]`

Load a component template.

**Parameters:**
- `component_path`: Relative path within component-framework/

**Returns:**
- Dictionary with keys: `path`, `frontmatter`, `content`

**Raises:**
- `FileNotFoundError`: If component not found
- `ValueError`: If frontmatter is missing or invalid

#### `render_component(component_path: str, variables: Dict[str, str]) -> str`

Render a component template with variable substitution.

**Parameters:**
- `component_path`: Relative path within component-framework/
- `variables`: Dictionary of `{{VARIABLE}}` -> value mappings

**Returns:**
- Rendered template content as string

#### `list_components(component_type: Optional[str] = None) -> List[str]`

List available components.

**Parameters:**
- `component_type`: Optional filter by type (memory, knowledge, tasks, etc.)

**Returns:**
- List of component paths (relative to framework directory)

#### `validate_component(component_path: str) -> List[str]`

Validate a component template.

**Parameters:**
- `component_path`: Relative path within component-framework/

**Returns:**
- List of validation error messages (empty if valid)

---

*Component Framework Implementation Plan v1.0.0*
*Prepared by: Dr. Sarah Kim, Technical Product Strategist & Engineering Lead*
*For execution by: senior-developer*
*Quality verification by: quality-reviewer*
*Program tracking by: software-program-manager*
