---
title: Agent Ecosystem Design Specification
description: Complete design for MD-frontmatter agent format, explicit tool invocation, and Domain/Workflow/Loom/Ecosystem builder pipeline
---

# Agent Ecosystem Design Specification

**Version:** 1.0.0
**Status:** Partially Implemented — Phase 5 delivered Python-class stages; MD-format registry loading and config/agents/ MD files remain as Phase 2 work. See Section 2.2 revision below.
**Date:** 2026-04-07
**Branch:** `feature/pipeline-orchestration-v1`
**Author:** planning-analysis-strategist (Dr. Sarah Kim)
**Audience:** Engineering leads, pipeline team, contributors coordinating with PR #720

---

## 1. Executive Summary

GAIA currently defines its pipeline agents through YAML configuration files in `config/agents/` that reference non-existent prompt files via a `system_prompt: prompts/some-file.md` pointer. This specification defines three interconnected systems to complete and extend this design.

**System A** replaces the two-file (YAML config + separate prompt) approach with a single `.md` file using YAML frontmatter for all metadata and a Markdown body as the system prompt. This eliminates dangling file references, co-locates agent identity with behavior, and unlocks full Markdown expressiveness — including code blocks, special characters, Unicode, and embedded tool invocations — in prompt bodies.

**System B** formalizes a syntax for embedding explicit tool invocations at specific workflow stages within prompt bodies. The pattern already exists in `master-ecosystem-creator.md` and `domain-analyzer.md` but is informal and inconsistent. This specification defines a fenced `tool-call` block syntax that is both human-readable and machine-parseable.

**System C** defines a four-stage meta-pipeline — Domain Analyzer, Workflow Modeler, Loom Builder, Ecosystem Builder — that takes a task description as input and generates a complete, executable set of agent definition files as output. The domain analyzer already exists; this specification specifies what must be built for the other three stages and how they connect.

The new `.md` agent format is designed as a strict superset of PR #720's `AgentManifest` schema, ensuring forward compatibility with itomek's user-defined agent mechanism while adding the pipeline-routing fields (triggers, capabilities, constraints) that the pipeline registry requires.

---

## 2. Current State Analysis

### 2.1 What Exists Today

**YAML agent configurations** (`config/agents/*.yaml`): Eighteen files define agent identity, routing signals, capabilities, tools, execution targets, and constraints. All eighteen use a nested `agent:` key structure. The `_load_agent()` method in `src/gaia/agents/registry.py` (lines 186-258) reads these files and produces `AgentDefinition` dataclass instances.

**`AgentDefinition` dataclass** (`src/gaia/agents/base/context.py`, lines 102-138): The canonical in-memory agent representation. Fields include: `id`, `name`, `version`, `category`, `description`, `model_id`, nested `capabilities` (`AgentCapabilities`), `triggers` (`AgentTriggers`), `system_prompt` (a plain string), `tools`, `execution_targets`, `constraints` (`AgentConstraints`), `metadata`, `enabled`, `load_count`, `last_used`.

**Informal `.md` agent format** (`/c/Users/amikinka/.claude/agents/*.md`): The `.claude/agents/` directory uses a different convention — YAML frontmatter with a free-form Markdown body — for Claude Code subagents. `domain-analyzer.md` and `master-ecosystem-creator.md` demonstrate this pattern. These files are not loaded by GAIA's `AgentRegistry`; they serve the Claude Code agent system separately.

**Domain Analyzer ecosystem** (`/c/Users/amikinka/.claude/agents/domain-analyzer/`): A complete multi-file Claude Code subagent ecosystem comprising `domain-analyzer.md` (main agent), `commands.md` (command implementations), and `output-templates/` (two templates: `analysis-report.md` and `agent-blueprint.md`). This is Stage 1 of System C.

### 2.2 What Is Missing

1. **Prompt bodies do not exist.** Every `config/agents/*.yaml` file contains `system_prompt: prompts/some-file.md` pointing to a file that has not been written. The `AgentDefinition.system_prompt` field is therefore always an empty string after load. **[PARTIALLY IMPLEMENTED in Phase 5 — `57ee63d`]** `src/gaia/utils/frontmatter_parser.py` now provides full MD-frontmatter parsing; the config-file body-as-prompt pattern infrastructure is in place. However, `registry.py` has not been wired to call `_load_md_agent()`, so existing YAML agents still resolve to empty system prompts.

2. **Formal tool invocation syntax — DELIVERED (Phase 5).** The `tool-call` fenced block syntax is now formally specified in `docs/guides/explicit-tool-calling.mdx` (commit `e952716`) and demonstrated in `component-framework/templates/agent-definition.md`. The syntax covers basic CALL, MCP CALL, CALL with prompt, and conditional IF/END IF blocks. No runtime parser exists yet (LLM-evaluated in Phase 1 scope); machine-parseable evaluation remains Phase 2 work as specified in Section 4.4 Scope Boundary.

3. **Pipeline stages — PARTIALLY DELIVERED (Phase 5) with architectural deviation.** Phase 5 delivered Python-class implementations of Stages 1–4 plus a GapDetector (Stage 4b) and PipelineExecutor (Stage 5):
   - Stage 1: `src/gaia/pipeline/stages/domain_analyzer.py` (`DomainAnalyzer(Agent)`) [IMPLEMENTED — `8d6ffdd`]
   - Stage 2: `src/gaia/pipeline/stages/workflow_modeler.py` (`WorkflowModeler(Agent)`) [IMPLEMENTED — `a32187c`]
   - Stage 3: `src/gaia/pipeline/stages/loom_builder.py` (`LoomBuilder(Agent)`) [IMPLEMENTED — `8dd22c1`]
   - Stage 4a: `src/gaia/pipeline/stages/gap_detector.py` (`GapDetector(Agent)`) [IMPLEMENTED — `fa3ef98`]
   - Stage 4b: `src/gaia/pipeline/stages/pipeline_executor.py` (`PipelineExecutor(Agent)`) [IMPLEMENTED — `0c5f294`]
   - Coordinator: `src/gaia/pipeline/orchestrator.py` (`PipelineOrchestrator(Agent)`) [IMPLEMENTED — `fa3ef98`]

   **Architectural deviation:** This spec anticipated MD-format agent config files in `config/agents/` (e.g., `workflow-modeler.md`). Phase 5 built Python subclasses of `Agent` instead. The MD-format "Ecosystem Builder" (`config/agents/ecosystem-builder.md`) — Stage 4 as originally designed — has not been built. The `master-ecosystem-creator.md` file serves a related but distinct role (Claude Code subagent for on-demand spawning). The MD-format pipeline described in Sections 5.3–5.5 remains as Phase 2 work for the registry-loadable agent definition system.

4. **Frontmatter parser delivered; registry MD loading still pending.** Phase 5 delivered `src/gaia/utils/frontmatter_parser.py` (410 LOC, commit `57ee63d`) — a complete frontmatter-aware Markdown parser with 493 unit tests. The `ComponentLoader` (`src/gaia/utils/component_loader.py`) uses it for template loading. However, `src/gaia/agents/registry.py` has NOT been updated to call `_load_md_agent()`. The `senior-dev-work-order.md` Task 2 (add `_load_md_agent()` to registry) remains unexecuted. Status: parser infrastructure delivered; registry integration pending. **[INFRASTRUCTURE IMPLEMENTED in Phase 5 — `57ee63d`; REGISTRY WIRING PENDING]**

5. **Capability vocabulary is not standardized.** Open Item 4 from the PR #720 analysis: the 18 YAML files use freeform capability strings with no validation against `src/gaia/core/capabilities.py`.

### 2.3 What Is Aspirational

The combined effect of Systems A, B, and C is an **agentic ecosystem that builds itself**: given a task description, the four-stage pipeline produces a set of `.md` agent definition files — complete with metadata, routing signals, and executable system prompts containing tool invocation sequences — that GAIA's registry can immediately load and route to.

---

## 3. New Agent Definition Format — YAML Frontmatter + Markdown Body

### 3.1 Format Specification

An agent definition file is a standard Markdown file where the YAML frontmatter block (delimited by `---` on the first and second occurrences of that delimiter) contains all agent metadata, and the Markdown body (everything after the second `---`) is the verbatim system prompt.

**File naming convention:** `{agent-id}.md`

**File location:** `config/agents/{agent-id}.md` for GAIA pipeline agents.

**Top-level structure:**

```
---
[YAML frontmatter — all metadata fields]
---

[Markdown body — the complete system prompt]
```

The YAML frontmatter block must be valid YAML. The Markdown body is not parsed as YAML under any circumstances; it is read as a raw string and passed verbatim as `AgentDefinition.system_prompt`. This means the body may contain any characters — including `---`, YAML-special characters, code blocks, backticks, curly braces, angle brackets, Unicode, and embedded tool invocation blocks — without escaping.

### 3.2 Frontmatter Fields — Complete Reference

All fields are documented below. Required fields must be present; optional fields have defaults.

```yaml
---
# ─── Identity (required) ──────────────────────────────────────────────────────

id: senior-developer                  # (required) Unique agent identifier. Must be
                                      # kebab-case, no spaces. Used as dict key in
                                      # registry, as agent_type in sessions DB.

name: Senior Developer                # (required) Human-readable display name. Used
                                      # in UI selector and log messages.

version: 1.0.0                        # (required) Semantic version string.

category: development                 # (required) One of the AGENT_CATEGORIES keys
                                      # defined in registry.py: planning, development,
                                      # review, management.

description: |                        # (required) Multi-line agent purpose description.
  Full-stack generalist agent         # Rendered in UI hover card. Loaded into
  capable of handling complex         # AgentDefinition.description.
  development tasks.

model_id: Qwen3-0.6B-GGUF            # (optional, default: inherit) Model to use.
                                      # "inherit" means use the session's configured
                                      # model. Maps to AgentDefinition.model_id.

enabled: true                         # (optional, default: true) Whether the registry
                                      # indexes this agent. Set to false to disable
                                      # without deleting the file.

# ─── Pipeline Routing Triggers (required for pipeline agents) ─────────────────

triggers:
  keywords:                           # Task description keywords that signal this
    - implement                       # agent should handle the task. Case-insensitive
    - develop                         # substring match. Maps to AgentTriggers.keywords.
    - code
  phases:                             # Pipeline phases where this agent is eligible.
    - DEVELOPMENT                     # Maps to AgentTriggers.phases. Valid values
    - REFACTORING                     # defined in src/gaia/pipeline/phases.py.
  complexity_range:                   # Complexity score range [0.0, 1.0] within which
    min: 0.3                          # this agent is eligible. Maps to
    max: 1.0                          # AgentTriggers.complexity_range.
  state_conditions: {}                # Optional dict of pipeline state key/value
                                      # conditions that must be true for activation.
                                      # Maps to AgentTriggers.state_conditions.
  defect_types: []                    # Defect type strings from DefectType enum
                                      # that this agent handles. Maps to
                                      # AgentTriggers.defect_types.

# ─── Capabilities (required for routing index) ────────────────────────────────

capabilities:                         # Semantic capability descriptors. Must use
  - full-stack-development            # vocabulary defined in
  - api-design                        # src/gaia/core/capabilities.py. Registry builds
  - database-design                   # _capability_index from these values.
  - testing
  - code-review
  - debugging
  - refactoring

# ─── Tools (required) ─────────────────────────────────────────────────────────

tools:                                # Functional tool bindings. Two vocabularies:
  - file_read                         # (1) GAIA pipeline tools: file_read, file_write,
  - file_write                        #     bash_execute, git_operations,
  - bash_execute                      #     search_codebase, run_tests, run_linters,
  - git_operations                    #     etc. Used by pipeline tool executor.
  - search_codebase                   # (2) PR #720 UI tools: rag, file_search,
  - run_tests                         #     file_io, shell, screenshot, sd, vlm.
                                      # Both vocabularies are valid here.
                                      # Maps to AgentDefinition.tools.

mcp_servers:                          # (optional) MCP servers this agent needs.
  - sequential-thinking               # Format: server name as string, or dict with
                                      # {name, args, env} for configured servers.
                                      # Maps to AgentManifest.mcp_servers (PR #720).

# ─── Execution (optional) ─────────────────────────────────────────────────────

execution_targets:                    # AMD hardware execution preferences.
  default: cpu                        # Maps to AgentDefinition.execution_targets and
  fallback:                           # AgentCapabilities.execution_targets.
    - gpu

# ─── Constraints (required for pipeline agents) ───────────────────────────────

constraints:
  max_file_changes: 20                # Maximum number of files this agent may modify
                                      # in a single execution. Maps to
                                      # AgentConstraints.max_file_changes.
  max_lines_per_file: 500             # Maximum lines per file change.
  requires_review: true               # Whether changes require human approval before
                                      # being applied. Pipeline gate control.
  timeout_seconds: 600                # Execution timeout. Maps to
                                      # AgentConstraints.timeout_seconds.
  max_steps: 100                      # Maximum reasoning/execution steps.

# ─── UI Compatibility (optional, PR #720 parity) ──────────────────────────────

conversation_starters:                # Suggested prompts shown in the UI welcome
  - "Implement a REST endpoint for..." # screen. Maps to AgentManifest.conversation_starters.
  - "Refactor this module to..."
  - "Add error handling to..."

color: blue                           # UI accent color for the agent card.

# ─── Metadata (required) ──────────────────────────────────────────────────────

metadata:
  author: GAIA Team                   # Who created this agent definition.
  created: "2026-04-07"               # ISO 8601 date string.
  tags:                               # Free-form searchable tags.
    - development
    - full-stack
    - core
---
```

### 3.3 Markdown Body as System Prompt

Everything after the closing `---` of the frontmatter block is the system prompt. It is read verbatim — no YAML parsing, no template expansion, no variable substitution by the registry itself. The registry sets `AgentDefinition.system_prompt` to the raw Markdown string.

The prompt body:

- May use any Markdown syntax: headings, bold, italic, tables, lists, blockquotes.
- May use any code fence language identifier, including the new `tool-call` identifier defined in Section 4.
- May contain `---` horizontal rules without triggering frontmatter parsing (the parser reads frontmatter only from the start of the file).
- May use curly braces, angle brackets, `$`, `@`, `#` in any quantity.
- May contain Unicode including emoji, CJK characters, RTL text, and mathematical symbols.
- May use `---` as a section separator freely.
- Should be structured for readability: the LLM uses the entire body as its behavioral specification.

**Recommended prompt body structure:**

```markdown
# {Agent Name} — {Role Title}

## Identity and Purpose

[One paragraph: who this agent is, what it does, when it is invoked.]

## Core Principles

[Bulleted list of behavioral invariants the agent must uphold in all circumstances.]

## Workflow

### Phase 1: {Phase Name}

[Prose description of what the agent does in this phase.]

[Optional tool-call blocks here — see Section 4.]

### Phase 2: {Phase Name}

[...]

## Output Specification

[What the agent produces, in what format, to whom.]

## Constraints and Safety

[Reiterate the key constraints from frontmatter in prose; the LLM needs behavioral context, not just metadata.]
```

### 3.4 Example: senior-developer.md — Full Conversion

The following is a complete conversion of `config/agents/senior-developer.yaml` into the new format, with a complete system prompt body that includes an explicit tool invocation section. This example is normative: all other agent conversions should follow this structure.

```markdown
---
id: senior-developer
name: Senior Developer
version: 1.0.0
category: development
description: |
  Full-stack generalist agent capable of handling complex development tasks
  across frontend, backend, and infrastructure. Activates during DEVELOPMENT
  and REFACTORING phases for medium-to-high complexity tasks.
model_id: Qwen3-0.6B-GGUF
enabled: true

triggers:
  keywords:
    - implement
    - develop
    - code
    - build
    - create
    - feature
    - endpoint
    - component
    - function
  phases:
    - DEVELOPMENT
    - REFACTORING
  complexity_range:
    min: 0.3
    max: 1.0
  state_conditions: {}
  defect_types: []

capabilities:
  - full-stack-development
  - api-design
  - database-design
  - testing
  - code-review
  - debugging
  - refactoring

tools:
  - file_read
  - file_write
  - bash_execute
  - git_operations
  - search_codebase
  - run_tests

execution_targets:
  default: cpu
  fallback:
    - gpu

constraints:
  max_file_changes: 20
  max_lines_per_file: 500
  requires_review: true
  timeout_seconds: 600
  max_steps: 100

conversation_starters:
  - "Implement a REST endpoint that does..."
  - "Refactor this module to improve..."
  - "Add comprehensive tests for..."
  - "Fix the bug in..."

color: blue

metadata:
  author: GAIA Team
  created: "2026-03-23"
  tags:
    - development
    - full-stack
    - core
---

# Senior Developer — Full-Stack Generalist

## Identity and Purpose

I am the Senior Developer agent for GAIA's pipeline orchestration system. I handle
complex, cross-cutting development tasks that span frontend, backend, and infrastructure
concerns. I am activated when the pipeline classifies a task as belonging to the
DEVELOPMENT or REFACTORING phase with a complexity score between 0.3 and 1.0.

My primary responsibility is to implement production-ready code that meets the
requirements handed to me from the planning phase, while respecting the constraints
established for this execution context. I do not make architectural decisions — those
belong to the Solutions Architect. I do not approve my own changes — those go to the
Quality Reviewer. I write code that works, is tested, and is ready for review.

## Core Principles

- **Read before writing.** Always inspect existing code before modifying it. Understand
  the pattern before extending it.
- **Minimal change surface.** Implement exactly what was specified. Resist the urge to
  refactor adjacent code that is not part of the task.
- **Test-first thinking.** For every function written, identify the test case before
  writing implementation code. Tests are not optional.
- **Respect constraints.** Never exceed `max_file_changes` (20) or `max_lines_per_file`
  (500) per execution. If the task requires more, flag it and split the work.
- **No silent failures.** If a requirement is ambiguous, unclear, or contradicts existing
  code, surface that ambiguity before writing code rather than making an assumption.

## Workflow

### Phase 1: Codebase Analysis

Before writing a single line of code, I analyze the existing codebase to understand
context, patterns, and conventions.

```tool-call
CALL: Grep "{primary_search_term}" "src/"
purpose: Find existing implementations related to the task
capture: existing_patterns
```

```tool-call
CALL: Read "{most_relevant_existing_file}"
purpose: Understand the pattern I must follow or extend
capture: reference_implementation
```

```tool-call
CALL: Bash "python -m pytest tests/ -x --co -q 2>/dev/null | head -40"
purpose: Understand current test structure and naming conventions
capture: test_conventions
```

### Phase 2: Requirements Validation

Before implementation, I validate that the task requirements are internally consistent
and achievable within the established constraints.

```tool-call
CALL: mcp__clear-thought__sequentialthinking -> requirements_analysis
prompt: |
  Analyze the implementation task for completeness and consistency:
  TASK: {task_description}
  EXISTING PATTERNS: {existing_patterns}
  REFERENCE IMPLEMENTATION: {reference_implementation}

  Step 1: Is the task fully specified? What is ambiguous?
  Step 2: Are there conflicts with the existing codebase patterns?
  Step 3: What is the minimal implementation surface (which files, which functions)?
  Step 4: What test cases must be written to validate correctness?
  Step 5: Are there any constraint violations (file count, line count, review gates)?
  Step 6: Produce a concrete implementation plan with ordered steps.
```

If any requirement is ambiguous after this analysis, I surface the ambiguity as a
structured question before proceeding. I do not implement against assumptions.

### Phase 3: Implementation

I implement the changes in the order specified by the implementation plan from Phase 2.

For each file to be created or modified:

1. If modifying an existing file, read it first with the Read tool.
2. Make the minimal change that satisfies the requirement.
3. Keep implementation consistent with `{reference_implementation}` patterns.
4. Add or update docstrings to match the codebase's documentation style.

```tool-call
IF: requirements_analysis.ambiguity_score > 0.3
CALL: mcp__clear-thought__structuredargumentation -> resolution
prompt: |
  Resolve the following implementation ambiguities through structured argumentation:
  AMBIGUITIES: {requirements_analysis.open_questions}
  EXISTING CODE CONTEXT: {existing_patterns}

  THESIS: The most consistent interpretation of each ambiguity is [X].
  ANTITHESIS: An alternative interpretation could be [Y] because [reason].
  SYNTHESIS: The correct resolution is [Z] with justification [reason].
END IF
```

### Phase 4: Test Writing

For every new function, method, or class I create, I write at least one test case
that verifies the happy path. For bug fixes, I write a regression test that would
have caught the bug.

```tool-call
CALL: Bash "python -m pytest {test_file_path} -xvs 2>&1 | tail -30"
purpose: Verify tests pass before submitting
capture: test_results
```

```tool-call
IF: test_results.contains("FAILED") OR test_results.contains("ERROR")
CALL: mcp__clear-thought__sequentialthinking -> failure_analysis
prompt: |
  Analyze the test failures and determine the correct fix:
  TEST OUTPUT: {test_results}
  IMPLEMENTATION: {implemented_code}

  Step 1: What is the exact failure message?
  Step 2: Is the failure in the test or in the implementation?
  Step 3: What is the minimal fix?
  Step 4: Does the fix introduce any regression risk?
END IF
```

### Phase 5: Change Summary

After implementation is complete, I produce a structured change summary for the
Quality Reviewer.

**Change Summary Format:**

```
FILES MODIFIED: [list of files with line count delta]
FILES CREATED:  [list of new files]
TEST COVERAGE:  [new tests added, existing tests affected]
CONSTRAINT CHECK:
  - File changes: N / 20 maximum
  - Lines per file: max N / 500 maximum
  - Requires review: YES
AMBIGUITIES RESOLVED: [list of any ambiguities resolved and how]
OPEN ITEMS: [anything the Quality Reviewer or human should check]
```

## Output Specification

My output is a set of file changes (via Write and Edit tools) plus the Change Summary
document above. I do not merge my own changes. I do not deploy. I flag the pipeline
to transition to the QUALITY phase after completing my change summary.

## Constraints and Safety

I operate within strict guardrails:

- **Maximum 20 files** may be modified or created in a single execution. If a task
  genuinely requires more, I split it and flag the split to the pipeline orchestrator.
- **Maximum 500 lines per file** for any single file I create or significantly modify.
  Existing files that already exceed this limit may be modified, but I do not extend them.
- **All changes require review.** I never apply changes directly to production paths.
  Every change I produce goes through the quality review gate.
- **Timeout: 600 seconds.** If I am approaching the timeout, I checkpoint my progress
  in a partial-work artifact and hand off to the pipeline with a clear resume point.
- **No credential handling.** If a task requires API keys, secrets, or credentials, I
  stop and request that they be provided through the appropriate secrets management path.
  I never write secrets into code files.
```

### 3.5 Syntax Support

The Markdown body supports the complete Markdown syntax surface. The following are explicitly confirmed as safe — each is a character class that might be mistakenly avoided:

| Syntax / Character | Supported | Notes |
|---|---|---|
| YAML `---` separator | Yes | Only parsed as frontmatter at file start |
| Code fences (triple backtick) | Yes | Any language identifier including `tool-call` |
| Curly braces `{}` | Yes | Used for variable references in tool-call blocks |
| `${}` template expressions | Yes | Not interpreted by registry |
| `<xml>` angle brackets | Yes | Not interpreted as HTML unless rendered in a browser |
| Unicode (CJK, emoji, RTL) | Yes | Python `open(..., encoding="utf-8")` handles all |
| Nested YAML-like structures | Yes | In body, all text is read as a raw string |
| `#` comments in prose | Yes | Treated as H1 headings or plain text per context |
| Pipe `\|` in tables | Yes | Standard Markdown table syntax |
| Backslash escapes | Yes | Standard Markdown escaping |
| HTML entities (`&amp;`) | Yes | Passed through verbatim |

---

## 4. Explicit Tool Invocation in Prompt Bodies

### 4.1 Design Rationale

Tool invocations embedded in prompt bodies serve a different purpose than tool invocations made by the LLM during inference. An embedded tool invocation is a **behavioral directive**: it tells the agent precisely when and how to call a specific tool during the execution of its workflow. The agent is expected to execute these calls at the specified workflow stage, not to decide opportunistically whether to use a tool.

This distinction matters because:

- It enables deterministic, auditable pipelines. An orchestrator can inspect the prompt body and know exactly what tools the agent will call and in what order.
- It prevents tool selection drift. Without explicit directives, LLMs may choose the wrong tool, skip required tool calls, or call tools in the wrong order.
- It formalizes the pattern already emerging in `master-ecosystem-creator.md` and `domain-analyzer.md`.

### 4.2 MCP Tool Invocation Pattern

MCP tool calls use the `mcp__{server-name}__{tool-name}` naming convention established by Claude Code. An MCP invocation block has the following structure:

````
```tool-call
CALL: mcp__{server-name}__{tool-name}
purpose: [human-readable description of why this call is made here]
prompt: |
  [multi-line prompt passed to the MCP tool]
  [may reference {variables} from previous captures]
capture: {output_variable_name}
```
````

**Required fields:**
- `CALL:` — the fully qualified MCP tool identifier
- `purpose:` — mandatory documentation of why this call occurs at this point

**Optional fields:**
- `prompt:` — the argument passed to the tool. May be multi-line (YAML block scalar). May reference `{variable_name}` placeholders that refer to outputs from earlier `capture:` declarations.
- `capture:` — a variable name that stores this tool's output for reference in subsequent tool-call blocks. Variable names use `snake_case`.

**Examples:**

Simple invocation without capture:
````
```tool-call
CALL: mcp__clear-thought__sequentialthinking
purpose: Validate requirements completeness before beginning implementation
prompt: |
  Analyze the task requirements for completeness:
  TASK: {task_description}
  Step 1: Are all inputs specified?
  Step 2: Are all outputs specified?
  Step 3: What is ambiguous?
```
````

Invocation with output capture:
````
```tool-call
CALL: mcp__clear-thought__collaborativereasoning -> expert_review
purpose: Multi-persona review of the proposed domain list before committing to taxonomy
prompt: |
  Four expert personas review this domain list:
  DOMAINS: {domain_list}
  Persona 1 — Software Architect: challenges architectural gaps
  Persona 2 — Domain Expert: challenges missing deep technical domains
  Persona 3 — DevOps: challenges missing operational domains
  Persona 4 — Product: challenges missing user-facing domains
```
````

The `-> variable_name` syntax is a shorthand for `capture: variable_name`. Both forms are valid.

### 4.3 Claude Code Native Tool Invocation Pattern

Claude Code's built-in tools (Read, Write, Edit, Bash, Grep, Glob) use a simplified invocation syntax:

````
```tool-call
CALL: {ToolName} {argument}
purpose: [description]
capture: {variable_name}
```
````

For tools that take a single path or string argument, the argument follows the tool name on the same line. For tools with multiple arguments, use named parameters:

````
```tool-call
CALL: Grep
pattern: "def {function_name}"
path: "src/"
purpose: Find all implementations of the target function
capture: function_locations
```
````

````
```tool-call
CALL: Bash
command: "python -m pytest tests/unit/ -xvs 2>&1 | tail -50"
purpose: Run unit tests and capture output for failure analysis
capture: test_output
```
````

Single-line forms for common cases:

````
```tool-call
CALL: Read "{file_path}"
purpose: Inspect existing implementation before modification
capture: existing_code
```
````

````
```tool-call
CALL: Glob "src/**/*.py"
purpose: Enumerate all Python source files for dependency analysis
capture: python_files
```
````

### 4.4 Conditional Tool Invocation

Conditional execution uses `IF:` / `END IF:` block wrappers. The condition is a free-form expression; it is evaluated by the LLM as a natural-language predicate against the named variables in scope.

````
```tool-call
IF: {variable}.{property} > {threshold} OR {condition_description}
CALL: mcp__clear-thought__structuredargumentation -> resolution
purpose: Resolve detected ambiguity before proceeding
prompt: |
  Resolve ambiguity in: {variable}.open_questions
  THESIS: [most consistent interpretation]
  ANTITHESIS: [alternative interpretation with justification]
  SYNTHESIS: [final resolution with explicit reasoning]
END IF:
```
````

The `ELSE:` clause is optional and specifies what happens when the condition is false:

````
```tool-call
IF: {test_output}.contains("FAILED")
CALL: mcp__clear-thought__sequentialthinking -> failure_analysis
purpose: Diagnose test failure before attempting fix
prompt: |
  Analyze the test failure:
  OUTPUT: {test_output}
  Step 1: Identify the exact assertion that failed
  Step 2: Determine whether the fault is in the test or the implementation
  Step 3: Propose the minimal fix
ELSE:
SKIP: All tests passed, proceed to change summary
END IF:
```
````

**Condition syntax rules:**

- `{variable}.contains("{string}")` — string membership test
- `{variable} > {number}` — numeric comparison (the LLM interprets {variable} as a score)
- `{variable} == "{string}"` — string equality
- `NOT {condition}` — negation
- `{condition_A} AND {condition_B}` — logical conjunction
- `{condition_A} OR {condition_B}` — logical disjunction
- Free-form English conditions are also valid: `IF: the implementation plan requires more than 20 file changes`

### 4.5 Tool Result Passing

When a tool-call block declares `capture: variable_name`, the captured output is available in all subsequent tool-call blocks in the same prompt body as `{variable_name}`. Reference a capture with curly braces:

- Reference the whole output: `{variable_name}`
- Reference a property: `{variable_name}.property_path`
- Reference in a prompt string: embed `{variable_name}` directly in the `prompt:` multi-line block

**Variable scope:** Variables are scoped to the current agent execution context. They are not shared across agent boundaries. When an agent calls another agent (in Stage 2+ pipeline scenarios), the calling agent passes explicitly named outputs; the callee does not inherit the caller's variables.

**Variable lifetime:** Variables persist for the duration of the current phase in which the tool-call block appears. Re-capture of the same variable name overwrites the previous value.

### 4.6 Full Example: Three-Stage Agent Workflow with Tool Calls

The following illustrates an agent prompt body (abridged) with tool calls at three distinct workflow stages.

```markdown
## Workflow

### Stage 1: Domain Discovery

I begin by understanding what the task requires, using sequential thinking to
surface all knowledge domains — including hidden and implicit ones.

```tool-call
CALL: mcp__clear-thought__sequentialthinking -> domain_list
purpose: Parse the task and identify all required knowledge domains, including hidden dependencies
prompt: |
  Analyze this task for domain decomposition:
  TASK: {task_description}

  Thought 1: What is being built, for whom, at what scale?
  Thought 2: What are the surface-level knowledge domains?
  Thought 3: What hidden/implicit domains does this silently depend on?
  Thought 4: What cross-cutting concerns apply (security, observability, error handling)?
  Thought 5: What integration domains are required (external systems, protocols)?
  Thought 6: What operational domains matter (deployment, monitoring, scaling)?
  Thought 7: Produce an initial ranked domain list (7-15 domains).
```

### Stage 2: Domain Validation

Before building the agent taxonomy, I validate that my domain list is
necessary and sufficient using the scientific method.

```tool-call
CALL: mcp__clear-thought__scientificmethod -> scored_domains
purpose: Test each domain for necessity and uniqueness; assign confidence scores
prompt: |
  Apply scientific method to domain hypothesis testing:
  OBSERVATION: The task requires building {task_description}
  HYPOTHESIS: The following domains are necessary and sufficient: {domain_list}
  EXPERIMENT: For each domain, ask:
    (a) Could the task be completed without this domain?
    (b) Does this domain add unique knowledge not covered by others?
  ANALYSIS: Score each domain: necessity (0-1), uniqueness (0-1), confidence (0-1)
  CONCLUSION: Finalized domain list with scores and inclusions/exclusions justified.
```

```tool-call
IF: {scored_domains}.min_confidence < 0.70
CALL: mcp__clear-thought__metacognitivemonitoring -> confidence_report
purpose: Flag low-confidence domains and recommend additional research
prompt: |
  Review the scored domain list for confidence gaps:
  SCORED DOMAINS: {scored_domains}
  For each domain with confidence < 0.70:
    - Identify the specific knowledge gap
    - Rate the gap's impact on analysis quality
    - Recommend a follow-up research action
  Overall analysis completeness: [0.0 - 1.0]
ELSE:
SKIP: All domains meet the 0.70 confidence threshold
END IF:
```

### Stage 3: Blueprint Generation

With validated domains, I generate the ecosystem blueprint artifact that
Stage 2 (Workflow Modeler) will consume.

```tool-call
CALL: Bash
command: "date -u +%Y-%m-%d"
purpose: Get current date for blueprint artifact timestamp
capture: today
```

```tool-call
CALL: Write
path: "output/blueprint-{task_slug}-{today}.md"
purpose: Write the machine-parseable ecosystem blueprint for handoff to Workflow Modeler
```

After the blueprint is written, I validate that it is parseable by reading it
back and checking for required section headers.

```tool-call
CALL: Grep
pattern: "^## Agent A[0-9]+"
path: "output/blueprint-{task_slug}-{today}.md"
purpose: Verify that agent taxonomy sections were written correctly
capture: agent_sections
```

```tool-call
IF: NOT {agent_sections}.count > 0
CALL: mcp__clear-thought__sequentialthinking -> repair_plan
purpose: Blueprint is malformed; diagnose and repair before handoff
prompt: |
  The blueprint file is missing required agent taxonomy sections.
  FILE CONTENT CHECK: {agent_sections}
  Step 1: What sections are present?
  Step 2: What sections are missing?
  Step 3: What caused the omission?
  Step 4: Produce the missing content.
END IF:
```
```

---

## 5. Domain/Workflow/Loom/Ecosystem Builder Architecture

### 5.1 System Overview

The four-stage pipeline transforms a free-text task description into an executable agent ecosystem. Each stage is an independent agent that produces a structured artifact consumed by the next stage. The stages may be invoked individually (a user may want only a domain analysis without building a full ecosystem) or as a complete pipeline.

```
[Task Description]
      |
      v
[Stage 1: Domain Analyzer]          <- exists at .claude/agents/domain-analyzer/
      | blueprint artifact
      v
[Stage 2: Workflow Modeler]         <- new agent to be built
      | workflow-model.md
      v
[Stage 3: Loom Builder]             <- new agent to be built
      | pipeline-config.yaml
      | agent-gap-list.md
      v
[Stage 4: Ecosystem Builder]        <- new agent to be built
      | config/agents/{id}.md files
      v
[GAIA AgentRegistry]                <- existing, extended to load .md files
      |
      v
[Pipeline Engine / RoutingEngine]   <- existing, unchanged
```

Each stage produces a named artifact file. Artifact files are the contract between stages. A stage may consume any artifact from any previous stage as long as it is available.

> **Implementation Status — 2026-04-08:** Phase 5 delivered Python-class implementations of all five pipeline stages (DomainAnalyzer, WorkflowModeler, LoomBuilder, GapDetector, PipelineExecutor) as `Agent` subclasses in `src/gaia/pipeline/stages/`. The MD-format config-file agents described in Sections 5.3–5.5 (`config/agents/workflow-modeler.md`, etc.) remain as planned artifacts for the registry-loadable agent system (Phase 2). The `FrontmatterParser` required for registry loading exists (`src/gaia/utils/frontmatter_parser.py`); the `_load_md_agent()` registry integration does not. Quality Gate 7 passed 13/13 using the Python-class implementations.

### 5.2 Stage 1: Domain Analyzer

**Agent file:** `/c/Users/amikinka/.claude/agents/domain-analyzer.md`
**Ecosystem directory:** `/c/Users/amikinka/.claude/agents/domain-analyzer/`
**Status:** Fully implemented. No new code required.

**Input:**
- `task_description` (string): free-text description of the task or project to be analyzed

**Processing (mandatory 9-step Clear Thought protocol):**
1. `mcp__clear-thought__sequentialthinking` — surface and hidden domain identification
2. `mcp__clear-thought__mentalmodel` (first_principles) — atomic knowledge primitives
3. `mcp__clear-thought__scientificmethod` — necessity/uniqueness/confidence scoring
4. `mcp__clear-thought__collaborativereasoning` — four-persona expert review
5. `mcp__clear-thought__decisionframework` (weighted-criteria) — taxonomy option scoring
6. `mcp__clear-thought__visualreasoning` — workflow flowchart and agent relationship graph
7. `mcp__clear-thought__metacognitivemonitoring` — confidence assessment and gap flagging
8. `mcp__clear-thought__structuredargumentation` — dialectical boundary validation
9. `mcp__clear-thought__designpattern` (agentic_design) — pattern assignment

**Primary output:** `analysis-report.md` rendered from `output-templates/analysis-report.md`. This file contains:
- Domain map with necessity/uniqueness/confidence scores
- Cross-cutting concerns section
- Stage-breakdown table and workflow diagram
- Agent taxonomy with typed inputs/outputs and interaction matrix
- Ecosystem summary (total agents, critical path, complexity estimate)
- Confidence assessment (per-domain scores, flagged domains, assumptions)

**Secondary output:** `blueprint-{task-slug}-{date}.md` rendered from `output-templates/agent-blueprint.md`, triggered by `/blueprint` command.

**Handoff to Stage 2:** The blueprint artifact. Stage 2 reads the "Agent Taxonomy" and "Agentic Workflow" sections programmatically. For this handoff to be reliable, the blueprint must use machine-parseable table formats (consistent column names, pipe-separated, no merged cells). This is already specified in the domain-analyzer `output-templates/analysis-report.md` template.

**Extension required:** The `agent-blueprint.md` output template should be extended with a new section:

```markdown
## Ecosystem Builder Handoff

<!-- MACHINE-PARSEABLE: This section is consumed directly by Stage 4 (Ecosystem Builder). -->
<!-- Each agent stub must include all required frontmatter fields. -->

### Agent Stub: {AGENT_ID}

```yaml
id: {AGENT_ID}
name: {AGENT_NAME}
category: {CATEGORY}
capabilities: [{DOMAIN_1}, {DOMAIN_2}]
triggers:
  keywords: [{KEYWORD_1}, {KEYWORD_2}]
  phases: [{PHASE}]
  complexity_range:
    min: {MIN}
    max: {MAX}
tools: [{TOOL_1}, {TOOL_2}]
```

**Role:** {ONE_SENTENCE_ROLE}
**Input contract:** {TYPED_INPUT_LIST}
**Output contract:** {TYPED_OUTPUT_LIST}
**Calls:** {AGENT_IDS_IT_CALLS}
**Called by:** {AGENT_IDS_THAT_CALL_IT}
**Tool invocation stages:** {PHASES_WHERE_TOOL_CALLS_OCCUR}
```

### 5.3 Stage 2: Workflow Modeler

**Agent file to create:** `config/agents/workflow-modeler.md` (GAIA pipeline agent)
or `/c/Users/amikinka/.claude/agents/workflow-modeler.md` (Claude Code subagent)
**Status:** Does not exist. Must be built.

**Role:** Transforms the high-level agentic workflow from Stage 1's blueprint into a precise execution graph with explicit data contracts, decision gate logic, parallelism specifications, and resource estimates. The output is concrete enough for Stage 3 to translate directly into GAIA pipeline configuration syntax.

**Input:**
- `blueprint_file` (path): path to the `blueprint-{task-slug}-{date}.md` artifact from Stage 1
- `gaia_config_dir` (path, optional): path to `config/agents/` to check which agents already exist

**Processing:**

The Workflow Modeler reads the blueprint's Agent Taxonomy and Agentic Workflow sections, then:

1. Expands each high-level stage into a concrete execution node with:
   - Exact agent assignment (which agent handles this stage)
   - Input data type and source (which prior stage produces it)
   - Output data type and consumer (which subsequent stage receives it)
   - Estimated token budget and timeout
2. Identifies parallel execution opportunities by analyzing data dependencies (two stages are parallelizable if neither depends on the other's output)
3. Formalizes decision gates: each gate becomes a named conditional with a true-path and false-path, both pointing to specific stage IDs
4. Assigns context-sharing requirements: which data must persist across agent boundaries vs. which is stage-local

**Output artifact:** `workflow-model-{task-slug}-{date}.md`

Content structure:

```markdown
## Execution Graph: {Task Name}

### Stage Registry

| Stage ID | Stage Name       | Agent ID          | Depends On | Parallel With | Timeout (s) |
|----------|-----------------|-------------------|------------|---------------|-------------|
| S1       | domain-analysis | domain-analyzer   | —          | —             | 120         |
| S2       | workflow-model  | workflow-modeler  | S1         | —             | 90          |
| S3       | loom-build      | loom-builder      | S2         | —             | 60          |
| S4a      | agent-gen-A1    | ecosystem-builder | S3         | S4b, S4c      | 120         |
| S4b      | agent-gen-A2    | ecosystem-builder | S3         | S4a, S4c      | 120         |

### Data Flow (Edge Table)

| Edge ID | From Stage | To Stage | Data Type        | Field Name           | Transform |
|---------|-----------|----------|-----------------|----------------------|-----------|
| E1      | S1        | S2       | markdown artifact | blueprint_file_path | none      |
| E2      | S2        | S3       | markdown artifact | workflow_model_path | none      |
| E3      | S3        | S4a      | yaml list        | agent_gap_list       | filter    |

### Decision Gates

| Gate ID | Stage | Condition                     | True Path | False Path           |
|---------|-------|-------------------------------|-----------|----------------------|
| G1      | S3    | all_agents_exist_in_registry  | skip S4   | proceed to S4a,b,c   |
| G2      | S4x   | agent_file_validates          | next stage| retry with correction |

### Shared Context Requirements

| Context Key     | Type   | Written By | Read By             | Persistence Scope |
|-----------------|--------|-----------|---------------------|-------------------|
| task_slug       | string | S1         | S2, S3, S4          | pipeline session   |
| blueprint_path  | path   | S1         | S2, S3, S4          | pipeline session   |
| today           | date   | S1         | S2, S3, S4          | pipeline session   |
```

### 5.4 Stage 3: Loom Builder / Pipeline Configurator

**Agent file to create:** `config/agents/loom-builder.md`
**Status:** Does not exist. Must be built.
**Name rationale:** "Loom" connects to `docs/spec/gaia-loom-architecture.md`, GAIA's existing architecture for pipeline orchestration. The Loom Builder's output is the concrete pipeline weave that the Loom architecture describes abstractly.

**Role:** Translates the execution graph from Stage 2 into GAIA's pipeline configuration format, checks which agents already exist in the registry, and produces both an executable configuration and a gap analysis for Stage 4.

**Input:**
- `workflow_model_path` (path): path to the workflow-model artifact from Stage 2
- `agents_dir` (path): `config/agents/` — the directory scanned by `AgentRegistry._load_all_agents()`
- `registry_categories` (dict): the `AGENT_CATEGORIES` dict from `registry.py` for validation

**Processing:**

1. Parse the Stage Registry table from the workflow model
2. For each stage, check whether the assigned agent ID has a corresponding `.md` or `.yaml` file in `agents_dir`
3. Produce the agent gap list: agent IDs that are assigned but do not have definition files
4. Translate the execution graph into GAIA pipeline configuration syntax (YAML)
5. Validate the configuration: all referenced agents exist, all data flow edges have matching output/input types, no circular dependencies

**Output artifacts:**

`pipeline-config-{task-slug}-{date}.yaml`:
```yaml
pipeline:
  id: {task-slug}
  version: 1.0.0
  stages:
    - id: S1
      agent: domain-analyzer
      depends_on: []
      parallel_with: []
      timeout_seconds: 120
      inputs:
        task_description: "{task_description}"
      outputs:
        blueprint_path: "output/blueprint-{task-slug}-{today}.md"
    - id: S2
      agent: workflow-modeler
      depends_on: [S1]
      inputs:
        blueprint_file: "{S1.blueprint_path}"
      outputs:
        workflow_model_path: "output/workflow-model-{task-slug}-{today}.md"
  gates:
    - id: G1
      after_stage: S3
      condition: "all_agents_exist"
      true_path: done
      false_path: S4
```

`agent-gap-list-{task-slug}-{date}.md`:
```markdown
## Agent Gap List

These agents are required by the pipeline but do not have definition files.
Stage 4 (Ecosystem Builder) must generate them.

| Agent ID          | Required By Stage | Category     | Priority |
|-------------------|-------------------|--------------|---------|
| workflow-modeler  | S2                | planning     | P1      |
| loom-builder      | S3                | planning     | P1      |
```

`loom-manifest-{task-slug}-{date}.md`: human-readable description of what the pipeline does, what agents it uses, what artifacts it produces, and what a human should verify before running it.

### 5.5 Stage 4: Ecosystem Builder

**Agent file to create:** `config/agents/ecosystem-builder.md`
**Status:** Does not exist. Must be built.
**Relationship to master-ecosystem-creator:** `master-ecosystem-creator.md` (in `.claude/agents/`) is a Claude Code subagent for creating complete Claude Code agent ecosystems (28+ components). The Ecosystem Builder is a GAIA pipeline agent whose specific function is generating GAIA `.md` agent definition files from the gap list produced by Stage 3. The two are complementary but not identical. The Ecosystem Builder uses the frontmatter spec (Section 3) and tool invocation spec (Section 4) defined in this document as its output template.

**Role:** For each agent in the gap list from Stage 3, generates a complete `.md` agent definition file conforming to the format specified in Section 3, with a real system prompt body appropriate for that agent's role and at least one embedded tool invocation section.

**Input:**
- `gap_list_path` (path): path to the `agent-gap-list-{task-slug}-{date}.md` from Stage 3
- `blueprint_path` (path): the Stage 1 blueprint, for agent spec stubs from the "Ecosystem Builder Handoff" section
- `workflow_model_path` (path): Stage 2 artifact, for tool invocation stage information

**Processing:**

For each agent in the gap list:

1. Read the agent's spec stub from the blueprint's "Ecosystem Builder Handoff" section
2. Use `mcp__clear-thought__sequentialthinking` to design the agent's behavioral specification
3. Generate the frontmatter block (using the spec from Section 3.2) populated from the stub
4. Generate the Markdown body (using the structure from Section 3.3) with:
   - Identity and purpose section
   - Core principles
   - Workflow phases with embedded tool-call blocks at appropriate stages
   - Output specification
   - Constraints section
5. Write the completed `.md` file to `config/agents/{agent-id}.md`
6. Validate: read the file back and verify frontmatter parses as valid YAML

**Output:**
- N new `.md` files in `config/agents/`
- `ecosystem-summary-{task-slug}-{date}.md` listing what was created and what to test

**Per-agent tool invocation sequence:**

```tool-call
CALL: mcp__clear-thought__sequentialthinking -> agent_spec_{agent_id}
purpose: Design the complete behavioral specification for this agent before writing
prompt: |
  Design the agent definition for: {agent_id}
  ROLE: {stub.role}
  DOMAINS: {stub.domains}
  INPUT CONTRACT: {stub.inputs}
  OUTPUT CONTRACT: {stub.outputs}
  TOOL INVOCATION STAGES: {stub.tool_invocation_stages}

  Step 1: What is this agent's core behavioral mandate?
  Step 2: What principles must it never violate?
  Step 3: What workflow phases does it have? (2-5 phases recommended)
  Step 4: At which phase should each tool call be embedded?
  Step 5: What does a complete, expert-quality system prompt look like for this agent?
```

### 5.6 Domain Analyzer Ecosystem Extension

The existing domain-analyzer ecosystem at `/c/Users/amikinka/.claude/agents/domain-analyzer/` is Stage 1. It needs the following targeted extensions to participate in the full pipeline:

**Extension 1: Update `output-templates/agent-blueprint.md`**

Add the "Ecosystem Builder Handoff" section specified in Section 5.2 above. This section must be appended to the existing template. No other sections of the template change. The extension adds machine-parseable agent stubs that Stage 4 can consume without reformatting.

**Extension 2: Add `/pipeline` command to `commands.md`**

A new command `/pipeline <task-description>` that:
1. Runs the full `/analyze` protocol
2. Automatically calls `/blueprint` to export the artifact
3. Returns the artifact path and a confirmation that Stage 2 can proceed
4. Does not invoke Stage 2 itself — it produces a clean handoff point

This command enables the full four-stage pipeline to be triggered from a single user invocation.

**Extension 3: No changes required to `domain-analyzer.md` itself**

The agent's 9-step reasoning protocol, output format, and command set are already well-designed. Adding the `/pipeline` command to `commands.md` is sufficient.

### 5.7 Integration with GAIA's AgentRegistry and Pipeline Routing

The GAIA `AgentRegistry` (`src/gaia/agents/registry.py`) currently loads only `*.yaml` and `*.yml` files via `_load_all_agents()`. To support the new `.md` format, the registry requires a targeted extension.

**Required changes to `registry.py`:**

`_load_all_agents()` must be extended to also discover `*.md` files:

```python
async def _load_all_agents(self) -> None:
    """Load all agent definitions from YAML and Markdown files."""
    if not self._agents_dir:
        return

    agent_files = list(self._agents_dir.glob("*.yaml"))
    agent_files.extend(self._agents_dir.glob("*.yml"))
    agent_files.extend(self._agents_dir.glob("*.md"))  # new

    for agent_file in agent_files:
        try:
            if agent_file.suffix == ".md":
                agent = await self._load_md_agent(agent_file)
            else:
                agent = await self._load_agent(agent_file)
            async with self._lock:
                self._agents[agent.id] = agent
        except Exception as e:
            logger.error(f"Failed to load agent from {agent_file}: {e}")
```

A new `_load_md_agent()` method parses the frontmatter and body:

```python
async def _load_md_agent(self, md_file: Path) -> AgentDefinition:
    """
    Load a single agent from a Markdown file with YAML frontmatter.

    The file must begin with --- and contain a second --- delimiter.
    Everything between the two delimiters is parsed as YAML.
    Everything after the second delimiter is used as the system_prompt verbatim.
    """
    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        raise AgentLoadError(f"{md_file}: does not begin with YAML frontmatter (---)")

    # Split on the second occurrence of --- on its own line
    parts = content.split("\n---\n", 2)
    if len(parts) < 2:
        raise AgentLoadError(f"{md_file}: no closing --- for frontmatter block")

    frontmatter_text = parts[0].lstrip("---").strip()
    system_prompt = parts[1].strip() if len(parts) > 1 else ""

    if yaml is None:
        raise ImportError("PyYAML is required for agent loading")

    data = yaml.safe_load(frontmatter_text)
    if not data:
        raise ValueError(f"{md_file}: empty frontmatter")

    # Reuse existing field parsing logic with the parsed frontmatter dict
    return self._build_agent_definition(data, system_prompt_override=system_prompt)
```

The existing `_load_agent()` field-parsing logic is extracted into a `_build_agent_definition()` helper that both loaders call. The `system_prompt_override` parameter allows `_load_md_agent()` to pass the Markdown body directly, bypassing the YAML `system_prompt:` field lookup.

**No changes required to:**
- `AgentDefinition` dataclass (fully compatible as-is)
- `AgentTriggers`, `AgentCapabilities`, `AgentConstraints` dataclasses
- `select_agent()` routing method
- Capability/trigger/category index building
- Hot-reload via watchdog (just add `*.md` to the watch pattern)

---

## 6. Implementation Plan

### 6.1 Phase 1: Format Migration — YAML to MD Frontmatter

**Scope:** Convert all 18 existing `config/agents/*.yaml` files to `config/agents/*.md`.

**Effort estimate:** 2–3 engineer-days (most effort is writing real prompt bodies, not the mechanical conversion).

**Steps:**

1. Write a migration script (`scripts/migrate_agents_yaml_to_md.py`) that:
   - Reads each `*.yaml` file
   - Extracts all frontmatter fields
   - Generates an `.md` file with the correct frontmatter block
   - Inserts a placeholder prompt body: `# {Agent Name}\n\n[System prompt body — to be completed by Phase 1 prompt authoring]`
   - Does NOT delete the `.yaml` files until Phase 2 validation is complete

2. Write prompt bodies for all 18 agents. The `senior-developer.md` example in Section 3.4 is the template. Priority order for authoring:
   - P1 (pipeline critical): `senior-developer`, `quality-reviewer`, `solutions-architect`, `planning-analysis-strategist`
   - P2 (development path): `backend-specialist`, `frontend-specialist`, `devops-engineer`, `data-engineer`
   - P3 (review path): `security-auditor`, `performance-analyst`, `accessibility-reviewer`, `test-coverage-analyzer`
   - P4 (management/supporting): all remaining agents

3. Validate each `.md` file against the frontmatter schema by running a validation script that calls `yaml.safe_load()` on the extracted frontmatter and checks for required fields.

4. Standardize `capabilities:` values against `src/gaia/core/capabilities.py` vocabulary. This resolves Open Item 4 from the PR #720 analysis.

**Acceptance criteria:**
- All 18 agents load successfully via `_load_md_agent()` without errors
- All `AgentDefinition` instances have a non-empty `system_prompt` field
- No capability value fails validation against the formal vocabulary

### 6.2 Phase 2: Registry Extension

**Scope:** Extend `src/gaia/agents/registry.py` to discover and load `*.md` files.

**Effort estimate:** 1 engineer-day including tests.

**Steps:**

1. Extract field-parsing logic from `_load_agent()` into `_build_agent_definition()` helper
2. Implement `_load_md_agent()` as specified in Section 5.7
3. Extend `_load_all_agents()` to glob `*.md` files
4. Extend the watchdog hot-reload pattern to watch `*.md` files
5. Write unit tests in `tests/unit/`:
   - `test_load_md_agent_minimal_frontmatter` — only required fields
   - `test_load_md_agent_full_frontmatter` — all fields including optional
   - `test_load_md_agent_no_frontmatter` — raises `AgentLoadError`
   - `test_load_md_agent_missing_required_field` — raises `AgentLoadError`
   - `test_load_md_agent_body_preserves_special_characters` — verifies raw string passthrough
   - `test_registry_discovers_both_yaml_and_md` — integration test loading a mixed directory

**Post-merge coordination with PR #720:** After PR #720 merges, rename our pipeline-scoped `AgentRegistry` class to `PipelineAgentRegistry` and move the file to `src/gaia/pipeline/agent_registry.py`. Update all imports. This is the registry naming resolution identified in the PR #720 analysis (Section 4.1).

### 6.3 Phase 3: Domain/Workflow Analyzer Extension

**Scope:** Extend the existing domain-analyzer ecosystem with the `/pipeline` command and the "Ecosystem Builder Handoff" section in the blueprint template.

**Effort estimate:** 0.5 engineer-days.

**Steps:**

1. Add "Ecosystem Builder Handoff" section to `/c/Users/amikinka/.claude/agents/domain-analyzer/output-templates/agent-blueprint.md`
2. Add `/pipeline` command specification to `/c/Users/amikinka/.claude/agents/domain-analyzer/commands.md`
3. Validate by running `/analyze` on a test task and confirming the blueprint output includes the new section with correctly formatted agent stubs

### 6.4 Phase 4: Loom Builder and Ecosystem Builder Agents

**Scope:** Build three new GAIA pipeline agents and one new Claude Code subagent.

**Effort estimate:** 3–4 engineer-days.

**Priority order:**

1. **Workflow Modeler** (`config/agents/workflow-modeler.md`): Takes blueprint artifact, produces execution graph. Unblocks Stages 3 and 4.

2. **Loom Builder** (`config/agents/loom-builder.md`): Takes execution graph, produces pipeline config + gap list. Unblocks Stage 4 and the pipeline configuration format.

3. **Ecosystem Builder** (`config/agents/ecosystem-builder.md`): Takes gap list, generates agent `.md` files. This is the agent that closes the loop — it uses this very specification as its behavioral template.

4. **Pipeline Orchestrator** (`config/agents/pipeline-orchestrator.md`): Optional coordinator agent that invokes Stages 1–4 in sequence, handles stage errors, and manages shared pipeline context. May be deferred if the pipeline engine's `routing_engine.py` can handle orchestration natively.

---

## 7. Interaction with PR #720

PR #720 (itomek's `feat: agent registry with per-session agent selection`) introduces an `AgentManifest` Pydantic schema and a UI-facing `AgentRegistry` that discovers agents from three sources: built-in Python agents, custom Python modules, and YAML manifests placed in `~/.gaia/agents/`. The new `.md` format specified in this document interacts with PR #720 in the following ways.

### 7.1 Format Compatibility

The new `.md` frontmatter format is a **strict superset** of PR #720's `AgentManifest` schema. Every field in `AgentManifest` has a direct mapping to a frontmatter field:

| PR #720 `AgentManifest` field | `.md` frontmatter field | Notes |
|---|---|---|
| `id` | `id` | Identical |
| `name` | `name` | Identical |
| `description` | `description` | Identical |
| `instructions` | [markdown body] | Body replaces the `instructions` string field |
| `tools` | `tools` | Same field name; PR #720 tools vocabulary is a subset of valid values |
| `models` | `model_id` + `execution_targets.model_fallbacks` | PR #720 uses an ordered list; we use a primary + fallback pattern |
| `conversation_starters` | `conversation_starters` | Identical |
| `mcp_servers` | `mcp_servers` | Identical |

Fields present in the `.md` format but absent from `AgentManifest`: `version`, `category`, `enabled`, `triggers`, `capabilities`, `execution_targets`, `constraints`, `metadata`. These are the pipeline-routing fields that PR #720's UI-facing registry does not need.

### 7.2 The Proposed `pipeline:` Manifest Extension

The PR #720 analysis (Section 5.5) proposed an extension to `AgentManifest` allowing YAML manifests to optionally declare pipeline-routing fields:

```yaml
pipeline:
  capabilities:
    - requirements-analysis
    - strategic-planning
  triggers:
    phases: [PLANNING]
    complexity_range: [0.3, 1.0]
```

The `.md` frontmatter format implements this proposal natively — the pipeline-routing fields are top-level fields, not nested under a `pipeline:` key, because in the `.md` format they are first-class citizens rather than extensions. For PR #720's YAML manifest format, the `pipeline:` extension remains the recommended path. This allows:

- Users who want simple UI-only custom agents to use the minimal PR #720 YAML manifest format
- Users who want pipeline-participating agents to use either the `.md` frontmatter format (for full control) or the PR #720 YAML manifest with the `pipeline:` extension (for lighter weight)

### 7.3 Registry Naming and Coexistence

As identified in the PR #720 analysis (Section 4.1), both branches created `src/gaia/agents/registry.py` with incompatible designs. The resolution is:

- PR #720's `AgentRegistry` (renamed `AgentDiscovery` or left as-is) handles UI-facing concerns: what agents exist, factory instantiation, conversation starters
- Our `AgentRegistry` is relocated to `src/gaia/pipeline/agent_registry.py` and renamed `PipelineAgentRegistry`, handling pipeline-facing concerns: `select_agent()`, phase/complexity/keyword routing, capability indexing

The `PipelineAgentRegistry.select_agent()` returns an agent ID. That ID is resolved via `AgentDiscovery.get(agent_id)` to get the factory. The two registries share agent IDs as their common key. This bridge is the `AgentOrchestrator` described in the PR #720 analysis as Open Item 1.

### 7.4 Vocabulary Resolution

After both registries exist, there are three capability-like field types in play:

1. `capabilities:` in `.md` frontmatter — semantic routing descriptors, validated against `src/gaia/core/capabilities.py`
2. `tools:` in PR #720 `AgentManifest` — functional tool bindings (closed enum: `rag`, `file_search`, `file_io`, `shell`, `screenshot`, `sd`, `vlm`)
3. Our `tools:` in `.md` frontmatter — GAIA pipeline tool names (open list: `file_read`, `bash_execute`, etc.)

The `.md` format resolves vocabulary proliferation by having both `capabilities:` and `tools:` as distinct fields with distinct semantics. This distinction should be documented in `docs/guides/custom-agent.mdx` (the guide PR #720 adds) as a note: "The `capabilities:` field describes what this agent knows how to do (used for pipeline routing). The `tools:` field describes what technical tools it is authorized to use (used for execution)."

---

## 8. Open Questions

The following questions require resolution before or during implementation. Each is labeled with the party best positioned to answer it.

**Q1 — Frontmatter parser edge case: files starting with BOM**
UTF-8 BOM (`\xef\xbb\xbf`) appears in some Windows-authored files before the first `---`. The `_load_md_agent()` implementation must strip any BOM before checking for `---`. Python's `open(..., encoding="utf-8-sig")` handles this automatically. Should the registry use `utf-8-sig` universally or detect BOM conditionally?
**Owner:** Engineering (registry implementation)

**Q2 — `tool-call` fence language identifier and standard tooling**
Using `tool-call` as a fenced code block language identifier means GitHub, VS Code, and standard Markdown renderers will render these blocks as plain text (no syntax highlighting). Is this acceptable? An alternative is `yaml` (enabling YAML highlighting) but risks confusion about whether the block is data or directive. A second alternative is using HTML comments (`<!-- TOOL-CALL: ... -->`) which render invisibly in HTML but are visible in raw Markdown. Recommendation is to accept plain rendering of `tool-call` blocks but this should be confirmed with the team.
**Owner:** Team design decision

**Q3 — Tool result capture and LLM execution model**
The `capture: variable_name` syntax assumes the LLM execution environment supports named variable binding across tool calls within a single session. Claude Code's native tool use supports this through conversation context. GAIA's pipeline engine (`engine.py`) must also support it if the tool-call blocks are to be executed by the pipeline rather than an LLM. How does the pipeline engine handle named captures? Does it inject them as additional context into subsequent tool calls, or does it expect the LLM to maintain them?
**Owner:** Engineering (pipeline engine team)

**Q4 — Ecosystem Builder and recursive self-generation**
The Ecosystem Builder (Stage 4) generates `.md` agent definition files. If the pipeline is asked to build an ecosystem that includes improvements to the Ecosystem Builder itself, it would generate a new version of its own agent file. This is a legitimate use case (self-improvement) but requires careful validation: the new file should not replace the running agent file mid-execution. Does the registry's hot-reload mechanism need a "generation lock" to prevent this?
**Owner:** Engineering (registry implementation)

**Q5 — Multi-model execution for parallel Stage 4 instances**
Stage 4 may need to generate N agent files in parallel (one per gap-list entry). GAIA's current execution model does not support running multiple instances of the same agent ID in parallel within a single pipeline session. The workflow modeler's execution graph assigns unique stage IDs (S4a, S4b, S4c...) but all route to the same `ecosystem-builder` agent. Does the pipeline engine support multiple concurrent instances of one agent type?
**Owner:** Engineering (pipeline engine team)

**Q6 — PR #720 `pipeline:` extension timing**
Should the `pipeline:` extension to `AgentManifest` be proposed to itomek before or after our branch merges? Proposing before merge allows the schema to be included in PR #720 itself. Proposing after merge requires a follow-up PR and risks the schema hardening without the extension. The PR #720 analysis (Section 6, P0 item 1) marks this as a before-merge action. This design spec supersedes that recommendation only if the team decides the `.md` format makes the `pipeline:` extension unnecessary.
**Owner:** Joint (our team + itomek coordination)

**Q7 — Quality Reviewer: unresolved tensions to check**

The quality reviewer should specifically verify:

1. **Section 3.2 `triggers.complexity_range` field format.** The current YAML uses `{min: 0.3, max: 1.0}` (a dict), but `AgentTriggers` stores it as a `Tuple[float, float]`. The `_load_agent()` code (registry.py lines 228-236) has a complex workaround for this. The new `.md` format should standardize on a list `[0.3, 1.0]` to match the dataclass's tuple representation and avoid the workaround. Verify this decision does not break existing `.yaml` loading.

2. **Section 4.4 condition syntax.** The conditional `IF:` syntax uses free-form English conditions as a fallback. This is intentionally human-readable but means conditions are not machine-parseable in the general case. If the pipeline engine ever needs to evaluate conditions itself (rather than delegating to the LLM), it will need a subset of conditions that are machine-parseable. The spec should either restrict conditions to a formal grammar or explicitly document that conditions are LLM-evaluated and cannot be statically analyzed.

3. **Section 5.7 `_load_md_agent()` split logic.** The implementation splits on `"\n---\n"` (newline-dash-dash-dash-newline). This breaks if the frontmatter closing `---` is at the very end of the file without a trailing newline, or if the file uses Windows line endings (`\r\n---\r\n`). The implementation must normalize line endings before splitting.

4. **Capability vocabulary enforcement.** Section 3.2 states capabilities are validated against `src/gaia/core/capabilities.py` at load time. Verify that this file exists, defines a complete vocabulary, and that a validation step has been added to `_build_agent_definition()`. If the vocabulary file does not yet exist (it may be aspirational), downgrade the statement to "capabilities should use values from the vocabulary defined in src/gaia/core/capabilities.py" and create the vocabulary file as a separate task.

---

*Document produced by planning-analysis-strategist (Dr. Sarah Kim) as part of the feature/pipeline-orchestration-v1 strategic planning workstream.*
*File: `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-design-spec.md`*
*Sequential thinking steps applied: four explicit reasoning passes covering frontmatter field mapping, tool invocation syntax design, four-stage pipeline architecture, and PR #720 compatibility analysis.*

---

## 9. Template Library Integration

*Section added by software-program-manager (pipeline iteration 1). Sourced from action-plan.md Deliverable 2 and Deliverable 5.*

---

### 9.1 Template Library Location and Purpose

The master template library lives at `/c/Users/amikinka/.claude/templates/`. It is a project-agnostic global resource — it is not checked into the GAIA repository and is not specific to any single ecosystem task. Every time the four-stage pipeline is invoked to build a new agent ecosystem, all four stages read from this library to produce their output artifacts.

The library exists for three reasons:

1. **Consistency enforcement.** Without a shared template, each pipeline run or each ecosystem-builder invocation would invent its own frontmatter layout, variable naming, and structural conventions. Templates guarantee that every generated `.md` agent file is structurally identical and differs only in content.

2. **Separation of structure from content.** The ecosystem builder's job is to populate content (agent identity, capabilities, tool invocations). The template library's job is to define structure (which YAML fields appear, in what order, with what inline documentation). Keeping these separate allows the template library to evolve independently of the ecosystem builder's prompting logic.

3. **Machine-readable stage contracts.** The pipeline artifact templates (`templates/pipeline/`) define the exact output format that each stage must produce and the exact input format the next stage expects. They are the API contract between stages, maintained in one place rather than duplicated in four agent prompts.

The library is maintained alongside this specification. When Section 3.2 (frontmatter field reference) changes, the corresponding `templates/agents/` files must be updated in the same authoring session. Section 9.5 documents this obligation explicitly.

---

### 9.2 Template Library Structure

The following annotated directory tree covers every file in the library, what artifact each file produces, and which pipeline stage or agent uses it.

```
/c/Users/amikinka/.claude/templates/
│
├── README.md
│       Purpose: Master index. Explains library organization, variable naming
│                convention, template selection guide (minimal vs full vs
│                tool-calling vs pipeline-stage), version history, and the
│                complete cross-reference table mapping each template to the
│                spec section it implements.
│       Produced by: this file exists to guide human authors and agents alike.
│       Used by: all pipeline stages (consulted before selecting a template).
│
├── agents/
│   │   Purpose: Agent definition templates — the source scaffolding for every
│   │            .md file placed in config/agents/ or .claude/agents/.
│   │
│   ├── agent-minimal.md
│   │       Produces: A minimal valid agent .md file with only the 8 required
│   │                 frontmatter fields and a stub prompt body.
│   │       Used by: Ecosystem Builder (Stage 4) — for low-priority gap-list
│   │                entries where a stub is acceptable; also used by humans
│   │                for rapid prototyping.
│   │       Required fields: id, name, version, category, description, triggers
│   │                        (keywords, phases, complexity_range), capabilities,
│   │                        tools, constraints.
│   │       Variables: {{AGENT_ID}}, {{AGENT_NAME}}, {{VERSION}}, {{CATEGORY}},
│   │                  {{DESCRIPTION}}, {{KEYWORDS_LIST}}, {{PHASES_LIST}},
│   │                  {{COMPLEXITY_MIN}}, {{COMPLEXITY_MAX}},
│   │                  {{CAPABILITIES_LIST}}, {{TOOLS_LIST}}
│   │
│   ├── agent-full.md
│   │       Produces: A complete agent .md file with all optional and required
│   │                 frontmatter fields, inline field comments, and a
│   │                 well-structured 5-section prompt body (Identity, Core
│   │                 Principles, Workflow, Output Specification, Constraints).
│   │       Used by: Ecosystem Builder (Stage 4) — for P1 priority gap-list
│   │                entries and for the senior-developer.md proof-of-concept.
│   │       Variables: All agent-minimal.md variables plus {{MODEL_ID}},
│   │                  {{MCP_SERVERS_LIST}}, {{EXECUTION_DEFAULT}},
│   │                  {{EXECUTION_FALLBACK_LIST}}, {{MAX_FILE_CHANGES}},
│   │                  {{MAX_LINES}}, {{REQUIRES_REVIEW}},
│   │                  {{TIMEOUT_SECONDS}}, {{MAX_STEPS}},
│   │                  {{CONVERSATION_STARTERS_LIST}}, {{COLOR}},
│   │                  {{AUTHOR}}, {{CREATED_DATE}}, {{TAGS_LIST}}
│   │
│   ├── agent-tool-calling.md
│   │       Produces: A complete agent .md file with a prompt body that
│   │                 demonstrates all four tool-call block patterns from
│   │                 Section 4: simple CALL, CALL with capture, CALL with
│   │                 embedded prompt block, and IF/END IF conditional CALL.
│   │                 This is the authoritative reference card for prompt authors.
│   │       Used by: Ecosystem Builder (Stage 4) — when generating agents that
│   │                require explicit, sequenced tool invocations. Also used by
│   │                human prompt authors learning the Section 4 syntax.
│   │       Variables: All agent-full.md variables plus {{PHASE_1_TOOL}},
│   │                  {{PHASE_1_PURPOSE}}, {{PHASE_2_MCP_TOOL}},
│   │                  {{PHASE_2_PROMPT}}, {{CAPTURE_VAR}},
│   │                  {{CONDITION_EXPRESSION}}
│   │
│   ├── agent-mcp-consumer.md
│   │       Produces: An agent .md file optimized for agents whose primary
│   │                 function is calling MCP servers (e.g., agents that chain
│   │                 sequentialthinking, structuredargumentation, and
│   │                 mentalmodel calls). Emphasizes the mcp_servers frontmatter
│   │                 field and MCP-style tool-call blocks with capture and
│   │                 result-passing patterns.
│   │       Used by: Ecosystem Builder (Stage 4) — when the domain analysis
│   │                identifies a reasoning-chain agent in the taxonomy.
│   │       Variables: {{AGENT_ID}}, {{MCP_SERVER_NAME}},
│   │                  {{MCP_TOOL_NAMES_LIST}}, {{REASONING_PROMPT_TEMPLATE}}
│   │
│   └── agent-pipeline-stage.md
│           Produces: An agent .md file designed to operate as a named pipeline
│                     stage node. Includes Input Contract, Processing (with
│                     tool-call blocks that write an artifact file), Output
│                     Contract, and Handoff Protocol sections. This is the
│                     template for Workflow Modeler, Loom Builder, and Ecosystem
│                     Builder agents themselves.
│           Used by: Ecosystem Builder (Stage 4) — when generating Stages 2, 3,
│                    or 4 agents, or any other agent that must produce a named
│                    artifact file and hand off to a downstream stage.
│           Variables: {{STAGE_ID}}, {{ARTIFACT_FILENAME_PATTERN}},
│                      {{NEXT_STAGE_AGENT}}, {{INPUT_CONTRACT}},
│                      {{OUTPUT_CONTRACT}}
│
├── components/
│   │   Purpose: Ecosystem component templates — non-agent files that an
│   │            ecosystem needs to function (commands, tasks, checklists,
│   │            knowledge bases, utilities).
│   │
│   ├── command.md
│   │       Produces: A command definition file following the domain-analyzer
│   │                 commands.md pattern: command header, preconditions,
│   │                 step-by-step execution protocol, output format, error
│   │                 handling.
│   │       Used by: Ecosystem Builder (Stage 4) — when generating command files
│   │                for new ecosystem agents.
│   │       Variables: {{COMMAND_NAME}}, {{COMMAND_SYNTAX}},
│   │                  {{PARENT_AGENT}}, {{DESCRIPTION}}, {{STEPS_LIST}}
│   │
│   ├── task.md
│   │       Produces: A reusable task workflow definition — a multi-step process
│   │                 that agents invoke as a unit. Includes task identity,
│   │                 input requirements, ordered step list with success criteria,
│   │                 output artifact definition, and rollback/failure handling.
│   │       Used by: Ecosystem Builder (Stage 4) — when the workflow model
│   │                identifies recurring task patterns that should be
│   │                encapsulated as named units.
│   │       Variables: {{TASK_NAME}}, {{TASK_TRIGGER}}, {{OWNER_AGENT}},
│   │                  {{STEPS_COUNT}}, {{OUTPUT_ARTIFACT_NAME}}
│   │
│   ├── checklist.md
│   │       Produces: A quality validation checklist with three tiers:
│   │                 Required checks (must all pass), Recommended checks
│   │                 (majority should pass), Advisory checks (informational).
│   │                 Includes overall pass/fail decision logic.
│   │       Used by: Ecosystem Builder (Stage 4) — for quality-reviewer and
│   │                validation agent ecosystems. Also used by the quality-
│   │                reviewer agent when generating task-specific checklists.
│   │       Variables: {{CHECKLIST_NAME}}, {{SCOPE}},
│   │                  {{REQUIRED_ITEMS_COUNT}}, {{RECOMMENDED_ITEMS_COUNT}}
│   │
│   ├── knowledge-base.md
│   │       Produces: A domain knowledge file — structured reference material
│   │                 that agents read to inform behavior on a specific topic.
│   │                 Sections: Domain Identity, Core Concepts, Best Practices,
│   │                 Anti-Patterns, Reference Examples, Related Domains.
│   │       Used by: Ecosystem Builder (Stage 4) — when the domain analysis
│   │                identifies knowledge artifacts that agents need.
│   │       Variables: {{DOMAIN_NAME}}, {{CATEGORY}},
│   │                  {{APPLICABLE_AGENTS}}, {{CORE_CONCEPTS}},
│   │                  {{BEST_PRACTICES_COUNT}}
│   │
│   └── utility.md
│           Produces: A utility or helper module — reusable logic, data, or
│                     configuration that multiple agents reference. Sections:
│                     Utility Identity (name, type: data|algorithm|reference|
│                     config), Interface Specification, Content, Usage Examples.
│           Used by: Ecosystem Builder (Stage 4) — when the workflow model
│                    identifies shared logic that should be factored into a
│                    named utility rather than duplicated across agents.
│           Variables: {{UTILITY_NAME}}, {{UTILITY_TYPE}},
│                      {{CONSUMING_AGENTS_LIST}}
│
├── pipeline/
│   │   Purpose: Pipeline artifact templates — the structured output/input
│   │            contract files for each of the four pipeline stages.
│   │
│   ├── domain-analysis-output.md
│   │       Produces: The structured output artifact of Stage 1 (Domain
│   │                 Analyzer). Mirrors the existing analysis-report.md and
│   │                 agent-blueprint.md but with additional machine-parseable
│   │                 section markers that Stage 2 (Workflow Modeler) expects.
│   │                 Contains YAML blocks for: Blueprint Metadata, Domain
│   │                 Registry, Agent Taxonomy, Workflow Specification, and
│   │                 the Ecosystem Builder Handoff section (spec Section 5.2).
│   │       Used by: Domain Analyzer (Stage 1) — as the output format template.
│   │                Workflow Modeler (Stage 2) — as the input format it parses.
│   │       Variables: {{TASK_SLUG}}, {{BLUEPRINT_DATE}}, {{TOTAL_DOMAINS}},
│   │                  {{TOTAL_AGENTS}}, {{CONFIDENCE_SCORE}}
│   │
│   ├── workflow-model.md
│   │       Produces: The execution graph artifact of Stage 2 (Workflow Modeler).
│   │                 Contains five tables: Stage Registry, Data Flow Edges,
│   │                 Decision Gates, Shared Context Requirements, and Resource
│   │                 Budget. This is the contract format that Stage 3 (Loom
│   │                 Builder) consumes to generate the executable pipeline YAML.
│   │       Used by: Workflow Modeler (Stage 2) — as the output format template.
│   │                Loom Builder (Stage 3) — as the input format it parses.
│   │       Variables: {{TASK_SLUG}}, {{DATE}}, {{STAGE_COUNT}},
│   │                  {{EDGE_COUNT}}, {{GATE_COUNT}}
│   │
│   ├── loom-topology.md
│   │       Produces: The GAIA pipeline configuration file produced by Stage 3
│   │                 (Loom Builder). Unlike the other pipeline templates which
│   │                 are Markdown with embedded YAML blocks, this template is
│   │                 structured as a Markdown document wrapping a primary YAML
│   │                 block: pipeline header, stages list (each with id, agent,
│   │                 depends_on, parallel_with, timeout_seconds, inputs,
│   │                 outputs), gates list, shared context block, and error
│   │                 handling block.
│   │       Used by: Loom Builder (Stage 3) — as the output format template.
│   │                Ecosystem Builder (Stage 4) — reads topology to understand
│   │                which agent IDs are already planned vs missing (gap list).
│   │       Variables: {{PIPELINE_ID}}, {{PIPELINE_VERSION}},
│   │                  {{STAGES_LIST}}, {{GATES_LIST}}
│   │
│   └── ecosystem-manifest.md
│           Produces: The final human-readable report produced by Stage 4
│                     (Ecosystem Builder). Sections: Manifest Header (task,
│                     date, pipeline run ID), Generated Files Table (file path,
│                     agent ID, priority, line count, validation status),
│                     Validation Summary (which agents loaded successfully),
│                     Test Recommendations, Next Steps integration checklist.
│           Used by: Ecosystem Builder (Stage 4) — as the output format for
│                    the completion report delivered to the human operator.
│           Variables: {{TASK_SLUG}}, {{DATE}}, {{GENERATED_FILE_COUNT}},
│                      {{VALIDATED_COUNT}}, {{FAILED_COUNT}}
│
└── meta/
        Purpose: Meta-level templates — formats used within other templates
                 or in cross-stage handoff sections.

    ├── ecosystem-handoff.md
    │       Produces: The "Ecosystem Builder Handoff" section content appended
    │                 to each domain-analyzer blueprint output (spec Section 5.2).
    │                 Contains a machine-parseable comment header and a per-agent
    │                 stub structure (YAML code block: id, name, category,
    │                 capabilities, triggers, tools, role, input/output contracts,
    │                 calls, called_by, tool_invocation_stages).
    │       Used by: Domain Analyzer (Stage 1) — renders this section once per
    │                agent in the taxonomy.
    │                Ecosystem Builder (Stage 4) — parses this section to build
    │                its gap list.
    │       Variables: {{AGENT_ID}}, {{AGENT_NAME}}, {{CATEGORY}},
    │                  {{CAPABILITIES_LIST}}, {{KEYWORDS_LIST}}, {{PHASES_LIST}},
    │                  {{COMPLEXITY_MIN}}, {{COMPLEXITY_MAX}}, {{TOOLS_LIST}},
    │                  {{ROLE}}, {{INPUT_CONTRACT}}, {{OUTPUT_CONTRACT}},
    │                  {{CALLS_LIST}}, {{CALLED_BY_LIST}},
    │                  {{TOOL_INVOCATION_STAGES}}
    │
    └── agent-stub.yaml
            Produces: A minimal YAML stub for a single agent — used inside
                      pipeline handoff documents and gap lists as the machine-
                      parseable minimal spec that Stage 4 expands into a full
                      .md file. Fields: id, name, category, role, capabilities,
                      triggers (keywords, phases, complexity_range as list),
                      tools, interface (inputs and outputs with typed contract),
                      calls, called_by.
            Used by: Domain Analyzer (Stage 1) — as the per-agent stub format
                     inside ecosystem-handoff.md.
                     Loom Builder (Stage 3) — as the stub format in the
                     agent-gap-list section of its output.
                     Ecosystem Builder (Stage 4) — reads stubs as the
                     authoritative input for agent generation.
            Variables: Same as ecosystem-handoff.md
```

---

### 9.3 How the Ecosystem Builder Uses Templates

The four-stage pipeline uses the template library as follows. This is a normative description — each stage agent's prompt body must implement this flow.

**Stage 1 — Domain Analyzer reads output template:**

1. Domain Analyzer receives a task description from the human operator.
2. It reads `templates/pipeline/domain-analysis-output.md` to understand the required output structure.
3. It reads `templates/meta/ecosystem-handoff.md` to understand the per-agent stub format it must produce in its "Ecosystem Builder Handoff" section.
4. For each agent it identifies in the taxonomy, it populates one `templates/meta/agent-stub.yaml` instance with real values.
5. It writes its complete blueprint artifact (one Markdown file with embedded YAML blocks) matching the `domain-analysis-output.md` structure.

**Stage 2 — Workflow Modeler reads pipeline template:**

1. Workflow Modeler receives the Stage 1 blueprint artifact as input.
2. It reads `templates/pipeline/workflow-model.md` to understand the required output structure (Stage Registry table, Data Flow edges, Decision Gates, Shared Context, Resource Budget).
3. It parses the Stage 1 blueprint's Agent Taxonomy and Ecosystem Builder Handoff sections to extract agent stubs.
4. It populates the workflow-model.md template with the execution graph derived from the stage relationships in the blueprint.
5. It writes the workflow model artifact matching the `workflow-model.md` structure.

**Stage 3 — Loom Builder reads topology template:**

1. Loom Builder receives the Stage 2 workflow model as input.
2. It reads `templates/pipeline/loom-topology.md` to understand the required YAML configuration format.
3. It reads `templates/pipeline/domain-analysis-output.md` to identify agent IDs that are required by the topology.
4. It compares required agent IDs against agents that exist in `config/agents/` (both `.yaml` and `.md` files).
5. Agents that are required but absent become the gap list. It writes the gap list using the per-agent stub format from `templates/meta/agent-stub.yaml`.
6. It writes the loom topology file matching the `loom-topology.md` structure.

**Stage 4 — Ecosystem Builder reads agent definition templates:**

1. Ecosystem Builder receives the gap list from Stage 3 as primary input and the loom topology as secondary input.
2. For each agent stub in the gap list, it selects the appropriate agent template:
   - Agent is a pipeline stage node (orchestrates other agents): use `templates/agents/agent-pipeline-stage.md`
   - Agent makes heavy use of MCP server calls: use `templates/agents/agent-mcp-consumer.md`
   - Agent has explicit, sequenced tool invocations: use `templates/agents/agent-tool-calling.md`
   - Agent is a domain specialist without special structural requirements: use `templates/agents/agent-full.md`
   - Agent is low-priority or a placeholder: use `templates/agents/agent-minimal.md`
3. For each selected template, it populates all `{{REQUIRED_VARIABLE}}` placeholders from the agent stub. Optional `{{?OPTIONAL_FIELD}}` placeholders are omitted if the stub does not supply a value.
4. It writes the populated template to `config/agents/{agent-id}.md`.
5. It validates each written file by running `yaml.safe_load()` against the frontmatter block.
6. It writes the final `templates/pipeline/ecosystem-manifest.md` populated with generation results.

---

### 9.4 Template Variable Reference

All template variables follow a two-tier naming convention:

| Convention | Syntax | Meaning | Behavior if missing |
|---|---|---|---|
| Required variable | `{{UPPER_SNAKE_CASE}}` | Must be replaced before the template is written to disk | Generation must halt and report the missing variable |
| Optional variable | `{{?UPPER_SNAKE_CASE}}` | May be omitted; the surrounding YAML key line is also removed | Template is written with that field omitted entirely |

The following table is the complete cross-template variable reference. Variables that appear in multiple templates have a single canonical definition.

| Variable | Type | Definition | Templates that use it |
|---|---|---|---|
| `{{AGENT_ID}}` | string | Kebab-case identifier, unique across all agents in config/agents/. Becomes the `id:` frontmatter field. | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{AGENT_NAME}}` | string | Human-readable display name. Becomes the `name:` frontmatter field. | all agent templates, ecosystem-handoff.md |
| `{{VERSION}}` | semver string | Agent version (e.g., `1.0.0`). Becomes the `version:` frontmatter field. | all agent templates |
| `{{CATEGORY}}` | string | Agent category (e.g., `development`, `analysis`, `quality`). | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{DESCRIPTION}}` | string | One-paragraph description of the agent's purpose. Becomes the `description:` frontmatter field. | all agent templates |
| `{{KEYWORDS_LIST}}` | YAML list | Trigger keywords (one per line, YAML list format). | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{PHASES_LIST}}` | YAML list | Pipeline phases in which this agent is eligible (e.g., `[DEVELOPMENT, REFACTORING]`). | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{COMPLEXITY_MIN}}` | float 0.0-1.0 | Lower bound of the complexity_range tuple. Combined with `{{COMPLEXITY_MAX}}` as `[{{COMPLEXITY_MIN}}, {{COMPLEXITY_MAX}}]`. | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{COMPLEXITY_MAX}}` | float 0.0-1.0 | Upper bound of the complexity_range tuple. | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{CAPABILITIES_LIST}}` | YAML list | Semantic routing descriptor strings. See action-plan.md Tension 4 for vocabulary status. | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{TOOLS_LIST}}` | YAML list | GAIA pipeline tool names authorized for this agent. | all agent templates, ecosystem-handoff.md, agent-stub.yaml |
| `{{MODEL_ID}}` | string | LLM model identifier (e.g., `Qwen3-0.6B-GGUF`). Becomes the `model_id:` frontmatter field. | agent-full.md, agent-tool-calling.md, agent-mcp-consumer.md, agent-pipeline-stage.md |
| `{{MCP_SERVERS_LIST}}` | YAML list | MCP server names this agent is permitted to call. | agent-full.md, agent-tool-calling.md, agent-mcp-consumer.md |
| `{{EXECUTION_DEFAULT}}` | string | Default execution target (`cpu`, `gpu`, `npu`). | agent-full.md, agent-tool-calling.md, agent-pipeline-stage.md |
| `{{EXECUTION_FALLBACK_LIST}}` | YAML list | Ordered fallback execution targets. | agent-full.md, agent-tool-calling.md, agent-pipeline-stage.md |
| `{{MAX_FILE_CHANGES}}` | integer | Maximum files that may be modified in one execution. | agent-full.md, agent-tool-calling.md, agent-pipeline-stage.md |
| `{{MAX_LINES}}` | integer | Maximum lines per file for files created or significantly modified. | agent-full.md, agent-tool-calling.md, agent-pipeline-stage.md |
| `{{REQUIRES_REVIEW}}` | boolean | Whether all changes require human review before applying. | agent-full.md, agent-tool-calling.md |
| `{{TIMEOUT_SECONDS}}` | integer | Maximum execution time in seconds before checkpoint and handoff. | agent-full.md, agent-tool-calling.md, agent-pipeline-stage.md |
| `{{MAX_STEPS}}` | integer | Maximum number of tool calls in one execution. | agent-full.md, agent-tool-calling.md |
| `{{CONVERSATION_STARTERS_LIST}}` | YAML list | Example prompts displayed in agent selector UI. | agent-full.md |
| `{{COLOR}}` | string | UI display color for this agent. | agent-full.md |
| `{{AUTHOR}}` | string | Author or team name for the metadata block. | agent-full.md |
| `{{CREATED_DATE}}` | ISO 8601 date string | Creation date in `"YYYY-MM-DD"` format. | agent-full.md |
| `{{TAGS_LIST}}` | YAML list | Metadata tags for search and categorization. | agent-full.md |
| `{{PHASE_1_TOOL}}` | string | Tool name used in Phase 1 of the tool-calling template workflow. | agent-tool-calling.md |
| `{{PHASE_1_PURPOSE}}` | string | Human-readable purpose description for the Phase 1 tool call. | agent-tool-calling.md |
| `{{PHASE_2_MCP_TOOL}}` | string | MCP tool name used in Phase 2 (format: `mcp__server__tool`). | agent-tool-calling.md |
| `{{PHASE_2_PROMPT}}` | string | Prompt body for the Phase 2 MCP tool call block. | agent-tool-calling.md |
| `{{CAPTURE_VAR}}` | string | Variable name used in `capture:` fields. Snake_case. | agent-tool-calling.md |
| `{{CONDITION_EXPRESSION}}` | string | LLM-evaluated condition expression for the IF: block (Phase 1 only — see Section 4.4 Scope Boundary). | agent-tool-calling.md |
| `{{MCP_SERVER_NAME}}` | string | Primary MCP server name for MCP-consumer agents. | agent-mcp-consumer.md |
| `{{MCP_TOOL_NAMES_LIST}}` | YAML list | List of MCP tool names this agent calls. | agent-mcp-consumer.md |
| `{{REASONING_PROMPT_TEMPLATE}}` | string | Default prompt template passed to the MCP reasoning tool. | agent-mcp-consumer.md |
| `{{STAGE_ID}}` | string | Pipeline stage identifier (e.g., `S1`, `S2a`). | agent-pipeline-stage.md |
| `{{ARTIFACT_FILENAME_PATTERN}}` | string | Filename pattern for the stage output artifact. | agent-pipeline-stage.md |
| `{{NEXT_STAGE_AGENT}}` | string | Agent ID of the downstream stage that consumes this stage's output. | agent-pipeline-stage.md |
| `{{INPUT_CONTRACT}}` | string | Description of what this stage expects as input (file format, required fields). | agent-pipeline-stage.md, ecosystem-handoff.md, agent-stub.yaml |
| `{{OUTPUT_CONTRACT}}` | string | Description of what this stage produces as output (file format, required fields). | agent-pipeline-stage.md, ecosystem-handoff.md, agent-stub.yaml |
| `{{COMMAND_NAME}}` | string | Command name (e.g., `/pipeline`, `/analyze`). | components/command.md |
| `{{COMMAND_SYNTAX}}` | string | Full command syntax with arguments. | components/command.md |
| `{{PARENT_AGENT}}` | string | Agent ID that owns this command. | components/command.md |
| `{{STEPS_LIST}}` | YAML list | Ordered execution steps for the command. | components/command.md |
| `{{TASK_NAME}}` | string | Task name (noun-phrase, e.g., `capability-gap-analysis`). | components/task.md |
| `{{TASK_TRIGGER}}` | string | Condition or event that causes an agent to invoke this task. | components/task.md |
| `{{OWNER_AGENT}}` | string | Agent ID responsible for executing this task. | components/task.md |
| `{{STEPS_COUNT}}` | integer | Total number of ordered steps in the task. | components/task.md |
| `{{OUTPUT_ARTIFACT_NAME}}` | string | Filename of the artifact this task produces. | components/task.md |
| `{{CHECKLIST_NAME}}` | string | Checklist name (e.g., `agent-file-validation-checklist`). | components/checklist.md |
| `{{SCOPE}}` | string | What this checklist validates (e.g., `generated .md agent files`). | components/checklist.md |
| `{{REQUIRED_ITEMS_COUNT}}` | integer | Count of Required-tier checklist items. | components/checklist.md |
| `{{RECOMMENDED_ITEMS_COUNT}}` | integer | Count of Recommended-tier checklist items. | components/checklist.md |
| `{{DOMAIN_NAME}}` | string | Knowledge domain name (e.g., `pipeline-routing`, `frontmatter-format`). | components/knowledge-base.md |
| `{{APPLICABLE_AGENTS}}` | YAML list | Agent IDs that should read this knowledge base entry. | components/knowledge-base.md |
| `{{CORE_CONCEPTS}}` | string | Key concepts section body (definitions, terminology). | components/knowledge-base.md |
| `{{BEST_PRACTICES_COUNT}}` | integer | Count of best practices entries. | components/knowledge-base.md |
| `{{UTILITY_NAME}}` | string | Utility name (kebab-case). | components/utility.md |
| `{{UTILITY_TYPE}}` | string | One of: `data`, `algorithm`, `reference`, `config`. | components/utility.md |
| `{{CONSUMING_AGENTS_LIST}}` | YAML list | Agent IDs that import or reference this utility. | components/utility.md |
| `{{TASK_SLUG}}` | string | Kebab-case task identifier used in artifact filenames. | all pipeline templates |
| `{{BLUEPRINT_DATE}}` | ISO 8601 date string | Date the domain analysis was produced. | pipeline/domain-analysis-output.md |
| `{{TOTAL_DOMAINS}}` | integer | Count of domains identified in domain analysis. | pipeline/domain-analysis-output.md |
| `{{TOTAL_AGENTS}}` | integer | Count of agents in the taxonomy. | pipeline/domain-analysis-output.md |
| `{{CONFIDENCE_SCORE}}` | float 0.0-1.0 | Domain analyzer's self-assessed confidence in the taxonomy. | pipeline/domain-analysis-output.md |
| `{{DATE}}` | ISO 8601 date string | Artifact production date (all pipeline stages except Stage 1). | pipeline/workflow-model.md, pipeline/loom-topology.md, pipeline/ecosystem-manifest.md |
| `{{STAGE_COUNT}}` | integer | Count of stages in the workflow model. | pipeline/workflow-model.md |
| `{{EDGE_COUNT}}` | integer | Count of data flow edges in the workflow model. | pipeline/workflow-model.md |
| `{{GATE_COUNT}}` | integer | Count of decision gates in the workflow model. | pipeline/workflow-model.md |
| `{{PIPELINE_ID}}` | string | Pipeline run identifier (kebab-case, includes task slug and date). | pipeline/loom-topology.md |
| `{{PIPELINE_VERSION}}` | semver string | Pipeline specification version. | pipeline/loom-topology.md |
| `{{STAGES_LIST}}` | YAML sequence | Populated list of stage definitions in loom topology format. | pipeline/loom-topology.md |
| `{{GATES_LIST}}` | YAML sequence | Populated list of gate definitions in loom topology format. | pipeline/loom-topology.md |
| `{{GENERATED_FILE_COUNT}}` | integer | Total .md files written by Stage 4. | pipeline/ecosystem-manifest.md |
| `{{VALIDATED_COUNT}}` | integer | Count of written files that passed frontmatter validation. | pipeline/ecosystem-manifest.md |
| `{{FAILED_COUNT}}` | integer | Count of written files that failed frontmatter validation. | pipeline/ecosystem-manifest.md |
| `{{ROLE}}` | string | One-sentence statement of this agent's role in the ecosystem. | meta/ecosystem-handoff.md, meta/agent-stub.yaml |
| `{{CALLS_LIST}}` | YAML list | Agent IDs this agent calls (downstream dependencies). | meta/ecosystem-handoff.md, meta/agent-stub.yaml |
| `{{CALLED_BY_LIST}}` | YAML list | Agent IDs that call this agent (upstream dependents). | meta/ecosystem-handoff.md, meta/agent-stub.yaml |
| `{{TOOL_INVOCATION_STAGES}}` | YAML list | Pipeline phases or workflow steps in which this agent invokes tools. | meta/ecosystem-handoff.md, meta/agent-stub.yaml |

---

### 9.5 Extending the Template Library

To add a new template to the library:

1. **Select the correct directory.** Agent definition templates go in `templates/agents/`. Pipeline artifact templates go in `templates/pipeline/`. Reusable component templates go in `templates/components/`. Cross-template format definitions go in `templates/meta/`.

2. **Follow the naming convention.** Agent templates: `agent-{variant}.md`. Component templates: `{component-type}.md`. Pipeline templates: `{pipeline-stage-artifact}.md`. Meta templates: `{purpose}.md` or `{purpose}.yaml`.

3. **Add required frontmatter to the template file itself.** Every template file must begin with a comment block (HTML comment `<!-- ... -->`) that declares: template name, purpose (one line), produced artifact description, which pipeline stage or agent uses it, all variable names with types and definitions, and the spec section number it implements. This comment block must be stripped when the template is instantiated.

4. **Define all variables in the `{{UPPER_SNAKE_CASE}}` convention.** Mark optional variables with `{{?` prefix. Never use variables that are not declared in the template's own comment block and in `templates/README.md`.

5. **Register the template in `templates/README.md`.** Add it to the directory tree with a one-line purpose description and to the Variable Index table with all new variables it introduces.

6. **Note the corresponding spec section.** In the template's comment block and in `README.md`, record which section of this design specification defines the structure the template implements. This creates the audit trail for keeping templates synchronized with the spec.

7. **Update version references.** If the new template adds a field that also appears in other templates, update those templates to include the field as an optional variable. Increment the template library version in `README.md`.

The template library does not have automated enforcement today. The maintenance obligation is: whenever this design specification is updated, the author of the spec change is responsible for identifying all templates that correspond to the changed section and updating those templates in the same authoring session. This obligation is documented in `templates/README.md` and tracked in the Phase 2 implementation checklist (Section 10).
