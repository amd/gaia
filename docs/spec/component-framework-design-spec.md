---
title: Component Framework Design Specification
description: Design specification for reusable templates and patterns that agents reference during pipeline execution
---

# Component Framework Design Specification

**Version:** 1.0.0
**Status:** DRAFT
**Author:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Date:** 2026-04-07
**Branch:** feature/pipeline-orchestration-v1

---

## 1. Executive Summary

### 1.1 Purpose

This specification defines the **Component Framework** — a top-level directory structure containing reusable templates and patterns that agents actively reference, read, write, and update DURING pipeline execution.

Unlike agent definitions (which define WHO an agent is), the Component Framework defines WHAT agents work with — the living documents, checklists, knowledge bases, and task patterns that form the shared workspace for multi-agent collaboration.

### 1.2 Key Distinctions

| Aspect | Agent Definitions (`.claude/agents/`) | Component Framework (`component-framework/`) |
|--------|--------------------------------------|---------------------------------------------|
| **Purpose** | Define agent identity, tools, triggers | Define reusable work patterns and templates |
| **Format** | YAML frontmatter + system prompt | Markdown templates with structured sections |
| **Usage** | Loaded once at agent initialization | Read/written/updated during agent execution |
| **Location** | Hidden directory (`.claude/`) | Top-level visible directory |
| **Mutability** | Static (agents don't modify themselves) | Dynamic (agents actively update these files) |
| **Scope** | Per-agent configuration | Cross-agent shared patterns |

### 1.3 Relationship to Existing Patterns

The Component Framework integrates with:

1. **GAIA Agent System** (`src/gaia/agents/`): Agents use component templates as tools
2. **Pipeline Engine** (`src/gaia/pipeline/`): Pipeline stages produce/consume component artifacts
3. **Template Library** (`/c/Users/amikinka/.claude/templates/`): Component Framework is the PROJECT-LOCAL instantiation of global templates
4. **Agent Ecosystem** (Design Spec): Each agent ecosystem has its own `component-framework/` directory

---

## 2. Component Framework Structure

### 2.1 Directory Layout

```
component-framework/
├── memory/                    # Memory templates for agent state management
│   ├── short-term-memory.md   # Working context for current task
│   ├── long-term-memory.md    # Persistent knowledge across sessions
│   ├── working-memory.md      # Active problem-solving scratchpad
│   └── episodic-memory.md     # Historical execution records
│
├── knowledge/                 # Knowledge base templates
│   ├── domain-knowledge.md    # Domain-specific reference material
│   ├── procedural-knowledge.md # How-to guides and procedures
│   ├── declarative-knowledge.md # Facts, definitions, concepts
│   └── knowledge-graph.md     # Structured knowledge relationships
│
├── tasks/                     # Task definition templates
│   ├── task-breakdown.md      # Hierarchical task decomposition
│   ├── task-dependency.md     # Task prerequisite mapping
│   ├── task-priority.md       # Priority/ranking framework
│   └── task-tracking.md       # Progress tracking and status
│
├── commands/                  # Command templates
│   ├── shell-commands.md      # Shell/bash command patterns
│   ├── git-commands.md        # Git operation patterns
│   ├── build-commands.md      # Build/compile command patterns
│   └── test-commands.md       # Test execution command patterns
│
├── documents/                 # Document templates
│   ├── design-doc.md          # Design document structure
│   ├── api-spec.md            # API specification structure
│   ├── meeting-notes.md       # Meeting notes structure
│   └── status-report.md       # Status report structure
│
└── checklists/                # Quality validation checklists
    ├── domain-analysis-checklist.md   # Domain analysis validation
    ├── workflow-modeling-checklist.md # Workflow model validation
    ├── code-review-checklist.md       # Code review validation
    └── deployment-checklist.md        # Deployment readiness
```

### 2.2 Template Format Specification

All Component Framework templates use **Markdown with YAML frontmatter**:

```markdown
---
template_id: <unique-identifier>
template_type: <memory|knowledge|tasks|commands|documents|checklists>
version: <semver>
created: <ISO-8601-date>
updated: <ISO-8601-date>
maintainer: <agent-id or team>
description: <one-line purpose>
schema_version: "1.0"
---

# Template Title

## Purpose

[Clear statement of what this template is for]

## Structure

[Template sections with placeholders]

## Usage Protocol

[How agents should read/write/update this template]

## Examples

[Concrete examples of populated templates]

## Related Components

[Links to related templates in the framework]
```

### 2.3 Agent Reference Patterns

Agents reference Component Framework templates using the following patterns:

#### 2.3.1 Read Pattern (R)

Agent loads a template to understand structure or retrieve information:

```markdown
TOOL-CALL:
  action: read
  target: component-framework/memory/working-memory.md
  purpose: Load current task context
```

#### 2.3.2 Write Pattern (W)

Agent creates or updates a template instance:

```markdown
TOOL-CALL:
  action: write
  target: component-framework/knowledge/domain-knowledge.md
  content: |
    [Populated template content]
  purpose: Persist new domain knowledge
```

#### 2.3.3 Update Pattern (U)

Agent modifies specific sections of an existing template:

```markdown
TOOL-CALL:
  action: edit
  target: component-framework/tasks/task-tracking.md
  section: "Progress Log"
  changes: |
    - Added entry for iteration #3
  purpose: Track completion status
```

#### 2.3.4 Link Pattern (L)

Agent creates cross-references between templates:

```markdown
See Also:
- [[component-framework/knowledge/domain-knowledge.md#API-Patterns]]
- [[component-framework/tasks/task-breakdown.md#Phase-2]]
```

---

## 3. Subdirectory Specifications

### 3.1 Memory Templates

**Purpose:** Provide structured formats for different types of agent memory state.

#### 3.1.1 Short-Term Memory (`short-term-memory.md`)

Holds immediate context for the current execution turn:

```markdown
---
template_id: short-term-memory
template_type: memory
version: 1.0.0
---

# Short-Term Memory

## Current Turn Context

- **Timestamp:** {{ISO-8601_TIMESTAMP}}
- **Active Agent:** {{AGENT_ID}}
- **Current Task:** {{TASK_DESCRIPTION}}
- **Execution Phase:** {{PHASE_NAME}}

## Immediate State

[Agent populates with current working context]

## Turn Output

[Agent records output produced this turn]
```

**Usage:** Every agent writes to short-term memory at the start of execution and updates at completion.

#### 3.1.2 Long-Term Memory (`long-term-memory.md`)

Persistent knowledge that survives across sessions:

```markdown
---
template_id: long-term-memory
template_type: memory
version: 1.0.0
---

# Long-Term Memory

## Learned Patterns

[Patterns discovered through repeated execution]

## Skill Repository

[Capabilities developed over time]

## Historical Context

[Significant events that shape future behavior]
```

**Usage:** Agents write here when learning is complete and patterns are validated.

#### 3.1.3 Working Memory (`working-memory.md`)

Active problem-solving scratchpad:

```markdown
---
template_id: working-memory
template_type: memory
version: 1.0.0
---

# Working Memory

## Problem Statement

[Current problem being solved]

## Working Hypotheses

[Hypotheses under consideration]

## Reasoning Trace

[Step-by-step reasoning process]

## Intermediate Results

[Partial results and findings]
```

**Usage:** Agents use this as scratch space during complex multi-turn reasoning.

#### 3.1.4 Episodic Memory (`episodic-memory.md`)

Historical execution records:

```markdown
---
template_id: episodic-memory
template_type: memory
version: 1.0.0
---

# Episodic Memory

## Execution Log

| Timestamp | Agent | Task | Outcome | Artifacts |
|-----------|-------|------|---------|-----------|
| {{TS}} | {{ID}} | {{TASK}} | {{RESULT}} | {{ARTIFACTS}} |

## Significant Episodes

[Notable executions worth remembering]
```

**Usage:** Agents append to this log after each significant execution.

### 3.2 Knowledge Templates

**Purpose:** Structured formats for different types of knowledge artifacts.

#### 3.2.1 Domain Knowledge (`domain-knowledge.md`)

```markdown
---
template_id: domain-knowledge
template_type: knowledge
version: 1.0.0
---

# Domain Knowledge: {{DOMAIN_NAME}}

## Core Concepts

[Key concepts and definitions]

## Terminology

| Term | Definition | Related |
|------|------------|---------|
| {{TERM}} | {{DEF}} | {{RELATED}} |

## Best Practices

[Validated approaches for this domain]

## Anti-Patterns

[What to avoid and why]

## Reference Implementations

[Code examples and patterns]
```

#### 3.2.2 Procedural Knowledge (`procedural-knowledge.md`)

```markdown
---
template_id: procedural-knowledge
template_type: knowledge
version: 1.0.0
---

# Procedure: {{PROCEDURE_NAME}}

## Purpose

[What this procedure accomplishes]

## Preconditions

[What must be true before starting]

## Steps

1. [Step 1 with success criteria]
2. [Step 2 with success criteria]
3. ...

## Postconditions

[What is true after completion]

## Error Handling

[Common failures and recovery steps]
```

#### 3.2.3 Declarative Knowledge (`declarative-knowledge.md`)

```markdown
---
template_id: declarative-knowledge
template_type: knowledge
version: 1.0.0
---

# Facts: {{SUBJECT_AREA}}

## Assertions

[Fact statements with confidence scores]

## Relationships

[How facts relate to each other]

## Sources

[Where each fact originated]
```

#### 3.2.4 Knowledge Graph (`knowledge-graph.md`)

```markdown
---
template_id: knowledge-graph
template_type: knowledge
version: 1.0.0
---

# Knowledge Graph

## Nodes

| Node ID | Type | Label | Properties |
|---------|------|-------|------------|
| {{ID}} | {{TYPE}} | {{LABEL}} | {{PROPS}} |

## Edges

| Source | Relationship | Target | Weight |
|--------|--------------|--------|--------|
| {{SRC}} | {{REL}} | {{TGT}} | {{W}} |

## Queries

[Common queries against this graph]
```

### 3.3 Task Templates

**Purpose:** Structured formats for task definition and tracking.

#### 3.3.1 Task Breakdown (`task-breakdown.md`)

```markdown
---
template_id: task-breakdown
template_type: tasks
version: 1.0.0
---

# Task Breakdown: {{TASK_NAME}}

## Parent Task

[Link to parent task if applicable]

## Subtasks

| ID | Name | Complexity | Estimated Effort | Dependencies |
|----|------|------------|------------------|--------------|
| 1 | {{NAME}} | {{SCORE}} | {{EFFORT}} | {{DEPS}} |

## Work Packages

[Groupings of related subtasks]

## Critical Path

[Sequence of dependent tasks determining timeline]
```

#### 3.3.2 Task Dependency (`task-dependency.md`)

```markdown
---
template_id: task-dependency
template_type: tasks
version: 1.0.0
---

# Task Dependencies

## Dependency Graph

```
Task A --> Task B --> Task C
  |          ^
  v          |
Task D ------+
```

## Dependency Types

| Type | Description | Example |
|------|-------------|---------|
| Finish-to-Start | B cannot start until A finishes | Code review after implementation |
| Start-to-Start | B cannot start until A starts | Testing starts when coding starts |
| Finish-to-Finish | B cannot finish until A finishes | Documentation finishes with code |
```

#### 3.3.3 Task Priority (`task-priority.md`)

```markdown
---
template_id: task-priority
template_type: tasks
version: 1.0.0
---

# Task Priority Matrix

## Priority Scores

| Task ID | Urgency (1-5) | Impact (1-5) | Effort (1-5) | Priority Score |
|---------|---------------|--------------|--------------|----------------|
| {{ID}} | {{U}} | {{I}} | {{E}} | {{(U*I)/E}} |

## Priority Tiers

| Tier | Tasks | Rationale |
|------|-------|-----------|
| P0 (Critical) | [List] | Must complete first |
| P1 (High) | [List] | Should complete early |
| P2 (Medium) | [List] | Can defer if needed |
| P3 (Low) | [List] | Nice to have |
```

#### 3.3.4 Task Tracking (`task-tracking.md`)

```markdown
---
template_id: task-tracking
template_type: tasks
version: 1.0.0
---

# Task Tracking: {{TASK_NAME}}

## Status

- **Current State:** {{PLANNING|IN_PROGRESS|BLOCKED|COMPLETE}}
- **Started:** {{DATE}}
- **Due:** {{DATE}}
- **Owner:** {{AGENT_ID}}

## Progress Log

| Date | Agent | Action | Outcome | Next |
|------|-------|--------|---------|------|
| {{DATE}} | {{ID}} | {{ACTION}} | {{OUTCOME}} | {{NEXT}} |

## Blockers

[List of current impediments]

## Completion Criteria

[What must be true to mark complete]
```

### 3.4 Command Templates

**Purpose:** Reusable command patterns for common operations.

#### 3.4.1 Shell Commands (`shell-commands.md`)

```markdown
---
template_id: shell-commands
template_type: commands
version: 1.0.0
---

# Shell Command Patterns

## File Operations

```bash
# Pattern: {{NAME}}
command_template: |
  {{COMMAND}} {{FLAGS}} {{ARGS}}
example: |
  cp -r src/ backup/
variables:
  - SRC_PATH
  - DST_PATH
```

## Process Management

[Command patterns for process control]

## System Information

[Command patterns for system introspection]
```

#### 3.4.2 Git Commands (`git-commands.md`)

```markdown
---
template_id: git-commands
template_type: commands
version: 1.0.0
---

# Git Command Patterns

## Branch Operations

```bash
# Create and switch to new branch
command_template: |
  git checkout -b {{BRANCH_NAME}}
example: |
  git checkout -b feature/new-agent
```

## Commit Operations

[Command patterns for commits]

## Remote Operations

[Command patterns for remotes]
```

#### 3.4.3 Build Commands (`build-commands.md`)

```markdown
---
template_id: build-commands
template_type: commands
version: 1.0.0
---

# Build Command Patterns

## Python Builds

```bash
# Build wheel
command_template: |
  python -m build --wheel --outdir {{DIST_DIR}}
```

## TypeScript Builds

[Command patterns for TS compilation]

## Multi-language Builds

[Command patterns for polyglot projects]
```

#### 3.4.4 Test Commands (`test-commands.md`)

```markdown
---
template_id: test-commands
template_type: commands
version: 1.0.0
---

# Test Command Patterns

## Unit Tests

```bash
# Run unit tests
command_template: |
  python -m pytest tests/unit/ -v --tb=short {{EXTRA_FLAGS}}
```

## Integration Tests

[Command patterns for integration testing]

## Coverage Reports

[Command patterns for coverage analysis]
```

### 3.5 Document Templates

**Purpose:** Structured formats for common document types.

#### 3.5.1 Design Document (`design-doc.md`)

```markdown
---
template_id: design-doc
template_type: documents
version: 1.0.0
---

# Design Document: {{FEATURE_NAME}}

## Overview

[What is being designed and why]

## Requirements

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| R1 | {{REQ}} | {{P}} | {{AC}} |

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|-------------------|-----------|
| {{D}} | {{OPTS}} | {{WHY}} |

## Architecture

[Diagrams and structural descriptions]

## Implementation Plan

[Phased approach to building]

## Open Questions

[Unresolved design issues]
```

#### 3.5.2 API Specification (`api-spec.md`)

```markdown
---
template_id: api-spec
template_type: documents
version: 1.0.0
---

# API Specification: {{API_NAME}}

## Endpoints

| Method | Path | Description | Auth Required |
|--------|------|-------------|---------------|
| {{M}} | {{P}} | {{DESC}} | {{AUTH}} |

## Request/Response Schemas

```yaml
{{ENDPOINT}}:
  request:
    {{SCHEMA}}
  response:
    {{SCHEMA}}
```

## Error Codes

| Code | Meaning | Recovery |
|------|---------|----------|
| {{CODE}} | {{MEANING}} | {{RECOVERY}} |
```

#### 3.5.3 Meeting Notes (`meeting-notes.md`)

```markdown
---
template_id: meeting-notes
template_type: documents
version: 1.0.0
---

# Meeting Notes: {{MEETING_NAME}}

## Metadata

- **Date:** {{DATE}}
- **Time:** {{TIME}}
- **Attendees:** {{LIST}}
- **Absent:** {{LIST}}

## Agenda

1. [Agenda item 1]
2. [Agenda item 2]

## Discussion

[Summary of key points discussed]

## Decisions Made

[List of decisions with owners]

## Action Items

| Item | Owner | Due Date | Status |
|------|-------|----------|--------|
| {{ITEM}} | {{OWNER}} | {{DUE}} | {{STATUS}} |
```

#### 3.5.4 Status Report (`status-report.md`)

```markdown
---
template_id: status-report
template_type: documents
version: 1.0.0
---

# Status Report: {{PROJECT_NAME}}

## Reporting Period

{{START_DATE}} to {{END_DATE}}

## Overall Status

[RAG status: Red/Amber/Green with rationale]

## Completed This Period

[List of accomplishments]

## Planned for Next Period

[List of upcoming work]

## Risks and Issues

| Type | Description | Impact | Mitigation |
|------|-------------|--------|------------|
| Risk | {{DESC}} | {{IMP}} | {{MIT}} |
| Issue | {{DESC}} | {{IMP}} | {{MIT}} |

## Metrics

| Metric | Target | Actual | Variance |
|--------|--------|--------|----------|
| {{M}} | {{T}} | {{A}} | {{V}} |
```

### 3.6 Checklist Templates

**Purpose:** Quality validation frameworks for specific activities.

#### 3.6.1 Domain Analysis Checklist (`domain-analysis-checklist.md`)

```markdown
---
template_id: domain-analysis-checklist
template_type: checklists
version: 1.0.0
---

# Domain Analysis Checklist

## Required Checks (Must All Pass)

- [ ] Domain boundaries clearly defined
- [ ] Key stakeholders identified
- [ ] Core terminology documented
- [ ] Existing systems catalogued
- [ ] Data sources identified

## Recommended Checks (Majority Should Pass)

- [ ] Historical context captured
- [ ] Regulatory constraints noted
- [ ] Performance requirements quantified
- [ ] Security requirements specified

## Advisory Checks (Informational)

- [ ] Industry best practices referenced
- [ ] Competitive landscape analyzed
- [ ] Technology trends assessed

## Pass/Fail Decision

**PASS:** All required checks pass AND >= 70% of recommended checks pass

**FAIL:** Any required check fails OR < 70% of recommended checks pass
```

#### 3.6.2 Workflow Modeling Checklist (`workflow-modeling-checklist.md`)

```markdown
---
template_id: workflow-modeling-checklist
template_type: checklists
version: 1.0.0
---

# Workflow Modeling Checklist

## Required Checks

- [ ] All workflow stages identified
- [ ] Stage dependencies mapped
- [ ] Input/output contracts defined
- [ ] Decision gates specified
- [ ] Error handling paths included

## Recommended Checks

- [ ] Parallel execution opportunities identified
- [ ] Timeout values assigned
- [ ] Resource budgets estimated
- [ ] Checkpoint locations defined
```

#### 3.6.3 Code Review Checklist (`code-review-checklist.md`)

```markdown
---
template_id: code-review-checklist
template_type: checklists
version: 1.0.0
---

# Code Review Checklist

## Required Checks

- [ ] Code compiles without errors
- [ ] All tests pass
- [ ] No security vulnerabilities introduced
- [ ] No performance regressions
- [ ] Documentation updated

## Recommended Checks

- [ ] Code follows style guidelines
- [ ] Functions have appropriate docstrings
- [ ] Error messages are helpful
- [ ] No code duplication
- [ ] Logging is appropriate
```

#### 3.6.4 Deployment Checklist (`deployment-checklist.md`)

```markdown
---
template_id: deployment-checklist
template_type: checklists
version: 1.0.0
---

# Deployment Checklist

## Pre-Deployment

- [ ] All tests pass in staging
- [ ] Rollback plan documented
- [ ] Monitoring configured
- [ ] Alert thresholds set
- [ ] Runbook updated

## Deployment Execution

- [ ] Deployment script tested
- [ ] Health checks passing
- [ ] Logs flowing correctly
- [ ] Metrics reporting

## Post-Deployment

- [ ] Smoke tests pass
- [ ] User-facing functionality verified
- [ ] Performance baseline established
- [ ] Incident response team briefed
```

---

## 4. Integration Architecture

### 4.1 Component Framework in the Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    Multi-Stage Pipeline                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐  │
│  │    Stage 1   │      │    Stage 2   │      │    Stage 3   │  │
│  │    Domain    │─────>│   Workflow   │─────>│    Loom      │  │
│  │   Analyzer   │      │    Modeler   │      │   Builder    │  │
│  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘  │
│         │                     │                     │          │
│         │  Reads:             │  Reads:             │  Reads:   │
│         │  - checklists/      │  - knowledge/       │  - tasks/ │
│         │  - memory/working   │  - memory/long-term │  - docs/  │
│         │                     │                     │          │
│         │  Writes:            │  Writes:            │  Writes:  │
│         │  - knowledge/       │  - tasks/           │  - cmds/  │
│         │  - memory/episodic  │  - memory/working   │  - memory │
│         │                     │                     │          │
└─────────┴─────────────────────┴─────────────────────┴──────────┘
              │                     │                     │
              └─────────────────────┴─────────────────────┘
                                    │
                                    v
                    ┌───────────────────────────────┐
                    │    component-framework/        │
                    │  (Shared workspace for all    │
                    │   agents during execution)    │
                    └───────────────────────────────┘
```

### 4.2 Component Loader Utility

A new utility in `src/gaia/utils/component_loader.py`:

```python
"""
GAIA Component Loader

Load and manage Component Framework templates.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


class ComponentLoader:
    """
    Component Framework template loader.

    The ComponentLoader provides:
    - Load component templates from component-framework/ directory
    - Parse YAML frontmatter
    - Render templates with variable substitution
    - Validate template structure
    """

    def __init__(self, framework_dir: Optional[Path] = None):
        """
        Initialize component loader.

        Args:
            framework_dir: Path to component-framework/ directory
        """
        self._framework_dir = framework_dir or Path("component-framework")
        self._loaded_components: Dict[str, Any] = {}

    def load_component(self, component_path: str) -> Dict[str, Any]:
        """
        Load a component template.

        Args:
            component_path: Relative path within component-framework/

        Returns:
            Dictionary with 'frontmatter' and 'content' keys
        """
        full_path = self._framework_dir / component_path

        if not full_path.exists():
            raise FileNotFoundError(f"Component not found: {component_path}")

        with open(full_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        # Normalize line endings
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        # Parse frontmatter
        if not content.startswith("---"):
            raise ValueError(f"Missing frontmatter in: {component_path}")

        parts = content.split("\n---\n", 2)
        if len(parts) < 2:
            raise ValueError(f"Invalid frontmatter in: {component_path}")

        frontmatter_text = parts[1]
        body = parts[2] if len(parts) > 2 else ""

        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise ValueError(f"Invalid frontmatter YAML in: {component_path}")

        return {
            "path": component_path,
            "frontmatter": frontmatter,
            "content": body.strip(),
        }

    def render_component(
        self,
        component_path: str,
        variables: Dict[str, str],
    ) -> str:
        """
        Render a component template with variable substitution.

        Args:
            component_path: Relative path within component-framework/
            variables: Dictionary of {{VARIABLE}} -> value mappings

        Returns:
            Rendered template content
        """
        component = self.load_component(component_path)
        content = component["content"]

        # Replace {{VARIABLE}} placeholders
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", value)

        return content

    def list_components(self, component_type: Optional[str] = None) -> List[str]:
        """
        List available components.

        Args:
            component_type: Optional filter by type (memory, knowledge, tasks, etc.)

        Returns:
            List of component paths
        """
        if not self._framework_dir.exists():
            return []

        components = []
        for md_file in self._framework_dir.rglob("*.md"):
            rel_path = str(md_file.relative_to(self._framework_dir))
            if component_type:
                # Check if component type matches directory
                if rel_path.startswith(component_type + "/"):
                    components.append(rel_path)
            else:
                components.append(rel_path)

        return sorted(components)

    def validate_component(self, component_path: str) -> List[str]:
        """
        Validate a component template.

        Args:
            component_path: Relative path within component-framework/

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        component = self.load_component(component_path)
        fm = component["frontmatter"]

        # Required frontmatter fields
        required_fields = [
            "template_id",
            "template_type",
            "version",
            "description",
        ]

        for field in required_fields:
            if field not in fm:
                errors.append(f"Missing required field: {field}")

        # Validate template_type value
        valid_types = ["memory", "knowledge", "tasks", "commands", "documents", "checklists"]
        if "template_type" in fm and fm["template_type"] not in valid_types:
            errors.append(
                f"Invalid template_type: {fm['template_type']}. "
                f"Must be one of: {valid_types}"
            )

        return errors
```

### 4.3 Agent Integration Pattern

Agents integrate with Component Framework through their tool definitions:

```yaml
# Example agent definition snippet
tools:
  - load_component
  - save_component
  - update_component
  - list_components
```

Agent system prompt references:

```markdown
## Component Framework Usage

When working on tasks, you have access to the Component Framework:

1. **Before starting work:** Load relevant checklists from `component-framework/checklists/`
2. **During execution:** Update `component-framework/memory/working-memory.md` with progress
3. **After completion:** Write lessons learned to `component-framework/knowledge/`
4. **For commands:** Use patterns from `component-framework/commands/` as templates
```

---

## 5. Implementation Tasks

### 5.1 Phase 1: Directory Structure

| Task | Description | Priority |
|------|-------------|----------|
| 1.1 | Create `component-framework/` directory at project root | P0 |
| 1.2 | Create subdirectories: memory/, knowledge/, tasks/, commands/, documents/, checklists/ | P0 |
| 1.3 | Add `.gitignore` entries for runtime files (e.g., `*.lock`) | P1 |

### 5.2 Phase 2: Template Population

| Task | Description | Priority |
|------|-------------|----------|
| 2.1 | Create all memory templates (4 files) | P0 |
| 2.2 | Create all knowledge templates (4 files) | P0 |
| 2.3 | Create all task templates (4 files) | P0 |
| 2.4 | Create all command templates (4 files) | P1 |
| 2.5 | Create all document templates (4 files) | P1 |
| 2.6 | Create all checklist templates (4 files) | P0 |

### 5.3 Phase 3: Component Loader Utility

| Task | Description | Priority |
|------|-------------|----------|
| 3.1 | Implement `src/gaia/utils/component_loader.py` | P0 |
| 3.2 | Add ComponentLoader to `src/gaia/utils/__init__.py` exports | P0 |
| 3.3 | Write unit tests for ComponentLoader | P0 |

### 5.4 Phase 4: Agent Integration

| Task | Description | Priority |
|------|-------------|----------|
| 4.1 | Update agent base class to include component tools | P0 |
| 4.2 | Update `config/agents/*.yaml` to reference component-framework | P1 |
| 4.3 | Add component-framework usage to agent prompts | P1 |

### 5.5 Phase 5: Pipeline Integration

| Task | Description | Priority |
|------|-------------|----------|
| 5.1 | Update pipeline stages to read/write component templates | P0 |
| 5.2 | Add component-framework paths to pipeline configuration | P0 |
| 5.3 | Document component-framework usage in pipeline docs | P1 |

---

## 6. Relationship to Master Ecosystem Creator

### 6.1 Master Ecosystem Creator Overview

The `master-ecosystem-creator.md` (to be created) is a meta-agent that orchestrates the creation of complete agent ecosystems for specific domains.

### 6.2 How Master Ecosystem Creator Uses Component Framework

```
Master Ecosystem Creator
         │
         ├── Generates agent-specific component folders
         │   └── For each agent in the ecosystem:
         │       └── Creates customized component templates
         │
         ├── Selects templates from global library
         │   └── Reads from /c/Users/amikinka/.claude/templates/
         │
         └── Instantiates project-local component-framework/
             └── Populates with domain-specific content
```

### 6.3 Template Selection Strategy

The Master Ecosystem Creator selects templates based on:

1. **Agent Type:** Pipeline-stage agents get different templates than specialist agents
2. **Domain Complexity:** Complex domains get more detailed knowledge templates
3. **Team Size:** Larger teams need more structured communication templates
4. **Project Phase:** Early projects need planning templates; mature projects need maintenance templates

### 6.4 Generated Structure

For a given agent ecosystem, Master Ecosystem Creator produces:

```
component-framework/
├── memory/
│   └── {{AGENT_ID}}-working-memory.md   # Per-agent working memory
├── knowledge/
│   ├── {{DOMAIN}}-knowledge.md          # Domain-specific knowledge
│   └── {{AGENT_ID}}-procedures.md       # Agent-specific procedures
├── tasks/
│   └── {{TASK_TYPE}}-breakdown.md       # Task-specific breakdowns
├── checklists/
│   └── {{DOMAIN}}-analysis-checklist.md # Domain-specific checklists
└── README.md                             # Ecosystem navigation guide
```

---

## 7. Variable Convention

All template variables use `{{UPPER_SNAKE_CASE}}` notation:

| Variable | Description | Example |
|----------|-------------|---------|
| `{{AGENT_ID}}` | Unique agent identifier | `domain-analyzer` |
| `{{AGENT_NAME}}` | Human-readable agent name | `Domain Analyzer` |
| `{{TASK_NAME}}` | Name of current task | `API Development` |
| `{{TIMESTAMP}}` | ISO-8601 timestamp | `2026-04-07T10:30:00Z` |
| `{{DOMAIN_NAME}}` | Domain being analyzed | `E-commerce APIs` |
| `{{PHASE_NAME}}` | Current pipeline phase | `PLANNING` |
| `{{STATUS}}` | Current status | `IN_PROGRESS` |

Optional variables use `{{?VARIABLE_NAME}}` notation and may be omitted entirely.

---

## 8. Validation and Quality Gates

### 8.1 Template Validation

Before a template is considered valid, it must:

1. Start with `---` YAML frontmatter delimiter
2. Contain all required frontmatter fields
3. Have a valid `template_type` value
4. Include at least one content section in the body

### 8.2 Runtime Validation

During pipeline execution:

1. Templates read successfully must be cached
2. Templates written must be validated before commit
3. Template cross-references must be verified

### 8.3 Quality Gates

| Gate | Criteria | Enforcement |
|------|----------|-------------|
| Structure | All directories exist | Pre-execution check |
| Content | All templates have valid frontmatter | Load-time validation |
| Consistency | Variable names match convention | Template validation script |
| Completeness | Required templates populated | Pipeline gate |

---

## 9. Appendix A: Example Usage in Pipeline

### 9.1 Domain Analyzer Agent Execution

```
Domain Analyzer Agent starts execution:

1. LOAD: component-framework/checklists/domain-analysis-checklist.md
   - Validates checklist structure
   - Reads all required/recommended/advisory checks

2. READ: component-framework/memory/working-memory.md
   - Retrieves current task context
   - Checks for any prior work on this domain

3. EXECUTE: Domain analysis per checklist items
   - For each required check, performs analysis
   - Records findings in working memory

4. WRITE: component-framework/knowledge/domain-knowledge.md
   - Structures findings per knowledge template
   - Adds terminology, best practices, anti-patterns

5. APPEND: component-framework/memory/episodic-memory.md
   - Logs execution with timestamp and outcome
   - Links to generated knowledge artifacts

6. UPDATE: component-framework/tasks/task-tracking.md
   - Marks domain analysis task as complete
   - Records completion criteria met
```

### 9.2 Workflow Modeler Agent Execution

```
Workflow Modeler Agent starts execution:

1. READ: component-framework/knowledge/domain-knowledge.md
   - Understands domain structure from prior analysis

2. LOAD: component-framework/checklists/workflow-modeling-checklist.md
   - Validates it has the right framework for modeling

3. READ: component-framework/memory/episodic-memory.md
   - Reviews historical workflow patterns

4. CREATE: component-framework/tasks/task-breakdown.md
   - Structures workflow as task hierarchy

5. WRITE: component-framework/documents/design-doc.md
   - Documents workflow design decisions

6. UPDATE: component-framework/memory/working-memory.md
   - Records workflow model for next stage
```

---

## 10. Appendix B: Migration Notes

### 10.1 For Existing Projects

Projects adopting Component Framework should:

1. Run the component-framework initialization script:
   ```bash
   gaia component-framework init
   ```

2. Review generated templates and customize for project needs

3. Update agent definitions to reference component-framework paths

### 10.2 Backward Compatibility

Component Framework does not modify existing agent definitions or pipeline configurations. It is an additive enhancement.

---

## 11. Open Questions

### 11.1 Template Inheritance

**Question:** Should templates support inheritance (e.g., a base template that specialized templates extend)?

**Status:** Deferred to Phase 2. Current templates are self-contained.

### 11.2 Component Versioning

**Question:** How should template version changes be managed across agent ecosystems?

**Status:** Out of scope for Phase 1. Will be addressed in Master Ecosystem Creator specification.

### 11.3 Cross-Project Sharing

**Question:** Should component-framework/ be shareable across projects (e.g., via git submodule)?

**Status:** Research needed. Some organizations may want standardized checklists across projects.

---

## 12. References

1. [Agent Ecosystem Design Spec](docs/spec/agent-ecosystem-design-spec.md)
2. [Agent Ecosystem Action Plan](docs/spec/agent-ecosystem-action-plan.md)
3. [Pipeline Engine Specification](src/gaia/pipeline/engine.py)
4. [Template Library Design](action-plan.md#Deliverable-2)

---

*Component Framework Design Spec v1.0.0*
*Produced by: Dr. Sarah Kim, Technical Product Strategist & Engineering Lead*
*Pipeline Iteration: Planning Analysis Phase*
