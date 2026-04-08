# Phase 5: Domain Analyzer + Agentic Ecosystem Creator — Implementation Plan

**Document Version:** 1.0
**Date:** 2026-04-07
**Status:** READY FOR KICKOFF
**Owner:** software-program-manager
**Program:** BAIBEL-GAIA Integration
**Phase:** 5 - Domain Analyzer + Agentic Ecosystem Creator
**Branch:** feature/pipeline-orchestration-v1

---

## Executive Summary

Phase 5 delivers a **self-building agent ecosystem** — a four-stage pipeline that transforms task descriptions into production-ready agent definitions. This phase builds upon the solid foundation from Phases 0-4 (foundation, agent system, integration, enterprise infrastructure, production hardening) to deliver autonomous agent generation capabilities.

**Phase 5 Duration:** 4 weeks (4 sprints x 1 week each)
**Target Completion:** Quality Gate 7 PASS
**Estimated LOC:** ~2,500 lines across 4 stage agents + pipeline infrastructure
**Estimated Tests:** ~340 tests at 100% pass rate, 85%+ coverage
**Estimated Documentation:** 12 MDX files (SDK reference + user guides)

### Phase 5 Objectives

| # | Objective | Description | Priority | Quality Gate |
|---|-----------|-------------|----------|--------------|
| 1 | **Domain Analyzer** | Stage 1: Analyze task descriptions, identify domains, produce blueprint with agent taxonomy | P0 | DOMAIN-001, DOMAIN-002, DOMAIN-003 |
| 2 | **Workflow Modeler** | Stage 2: Generate execution graph with stage dependencies, data flows, decision gates | P0 | GENERATION-001, GENERATION-002 |
| 3 | **Loom Builder** | Stage 3: Build GAIA Loom topology, identify agent gaps, produce gap list | P0 | GENERATION-003, ORCHESTRATION-001 |
| 4 | **Ecosystem Builder** | Stage 4: Generate agent definitions from templates, validate, produce ecosystem manifest | P1 | ORCHESTRATION-002, ORCHESTRATION-003 |
| 5 | **Integration + Validation** | End-to-end pipeline execution, generated agent validation, documentation | P1 | INTEGRATION-001, INTEGRATION-002 |
| 6 | **Thread Safety + Performance** | Concurrent pipeline execution, 100+ thread validation | P1 | THREAD-007 |

---

## Proposed Components

### Component Overview

| Component | File | LOC Est | Tests | Priority | Quality Gate |
|-----------|------|---------|-------|----------|--------------|
| **Domain Analyzer** | `config/agents/domain-analyzer.md` | ~900 | N/A | P0 | DOMAIN-001 |
| **Workflow Modeler** | `config/agents/workflow-modeler.md` | ~800 | N/A | P0 | GENERATION-001 |
| **Loom Builder** | `config/agents/loom-builder.md` | ~700 | N/A | P0 | GENERATION-002 |
| **Ecosystem Builder** | `config/agents/ecosystem-builder.md` | ~900 | N/A | P1 | GENERATION-003 |
| **Pipeline Orchestrator** | `config/agents/pipeline-orchestrator.md` | ~500 | N/A | P2 | ORCHESTRATION-001 |
| **Template Library** | `/c/Users/antmi/.claude/templates/` | 17 files | N/A | P0 | GENERATION-003 |
| **Registry Extension** | `src/gaia/agents/registry.py` | ~200 | 10 | P0 | INTEGRATION-001 |

---

## Quality Gate 7 Criteria (Proposed)

### Domain Analyzer Criteria

| ID | Metric | Target | Test Method | Validation |
|----|--------|--------|-------------|------------|
| **DOMAIN-001** | Entity extraction accuracy | >90% | Compare extracted entities vs ground truth | Unit tests |
| **DOMAIN-002** | Boundary detection | 100% | Verify all domain boundaries correctly identified | Integration tests |
| **DOMAIN-003** | Complexity assessment validity | >85% correlation | Compare with human expert assessments | Benchmark tests |

### Agent Generation Criteria

| ID | Metric | Target | Test Method | Validation |
|----|--------|--------|-------------|------------|
| **GENERATION-001** | Generated code compiles | 100% | All generated Python/TypeScript files parse without errors | Unit tests |
| **GENERATION-002** | Generated tools functional | 100% | All `@tool` decorated functions execute without runtime errors | Integration tests |
| **GENERATION-003** | Generated prompts coherent | 100% | LLM evaluation confirms prompt body is actionable and consistent | LLM judge tests |

### Orchestration Criteria

| ID | Metric | Target | Test Method | Validation |
|----|--------|--------|-------------|------------|
| **ORCHESTRATION-001** | Agent selection accuracy | >90% | Selected agent matches task requirements per human evaluation | Benchmark tests |
| **ORCHESTRATION-002** | Task distribution efficiency | <10% idle time | Measure stage wait times in parallel execution | Performance tests |
| **ORCHESTRATION-003** | Result coherence | 100% | Final artifact passes all validation checks without manual intervention | Integration tests |

### Integration Criteria

| ID | Metric | Target | Test Method | Validation |
|----|--------|--------|-------------|------------|
| **INTEGRATION-001** | E2E pipeline execution | PASS | Full 4-stage pipeline produces loadable agent from task description | E2E tests |
| **INTEGRATION-002** | Generated agents functional | PASS | Generated agents execute their intended workflow without errors | Agent tests |
| **THREAD-007** | Thread safety | 100+ concurrent threads | Pipeline stages execute concurrently without race conditions | Stress tests |

---

## Timeline (4 Weeks)

### Week 1: Foundation + Registry Extension (Days 1-5)

**Status:** MILESTONE 1 — FOUNDATION

| Day | Task | Deliverable | Owner |
|-----|------|-------------|-------|
| 1 | Apply 8 spec edits to design spec | `agent-ecosystem-design-spec.md` updated | senior-developer |
| 2 | Extract `_build_agent_definition()` helper | `registry.py` refactored | senior-developer |
| 3 | Implement `_load_md_agent()` | CRLF/BOM handling in `registry.py` | senior-developer |
| 4 | Extend `_load_all_agents()` with collision guard | `.md` file discovery | senior-developer |
| 5 | Write `senior-developer.md` proof-of-concept | `config/agents/senior-developer.md` | senior-developer |

**Week 1 Quality Gate:** All 5 P0 tasks complete, quality-reviewer verification PASS

---

### Week 2: Template Library + Unit Tests (Days 6-10)

**Status:** MILESTONE 2 — INTEGRATION

| Day | Task | Deliverable | Owner |
|-----|------|-------------|-------|
| 6 | Write 10 unit tests for `_load_md_agent()` | `tests/unit/test_load_md_agent.py` | test-engineer |
| 7 | Create template library structure | `/c/Users/antmi/.claude/templates/` directory | technical-writer-expert |
| 8 | Write 5 agent templates | `templates/agents/*.md` | technical-writer-expert |
| 9 | Write 5 component templates | `templates/components/*.md` | technical-writer-expert |
| 10 | Write 6 pipeline/meta templates | `templates/pipeline/*.md`, `templates/meta/*.md` | technical-writer-expert |

**Week 2 Quality Gate:** All 10 unit tests PASS, all 17 template files created, README.md complete

---

### Week 3: Stage Agents Implementation (Days 11-15)

**Status:** MILESTONE 3 — ECOSYSTEM BUILDER

| Day | Task | Deliverable | Owner |
|-----|------|-------------|-------|
| 11 | Implement Stage 1: Domain Analyzer | `config/agents/domain-analyzer.md` | senior-developer |
| 12 | Implement Stage 2: Workflow Modeler | `config/agents/workflow-modeler.md` | senior-developer |
| 13 | Implement Stage 3: Loom Builder | `config/agents/loom-builder.md` | senior-developer |
| 14 | Implement Stage 4: Ecosystem Builder | `config/agents/ecosystem-builder.md` | senior-developer |
| 15 | Optional: Pipeline Orchestrator | `config/agents/pipeline-orchestrator.md` | senior-developer |

**Week 3 Quality Gate:** All 4 stage agents load via `_load_md_agent()`, tool-call blocks conform to Section 4 syntax

---

### Week 4: Integration + Quality Gate 7 (Days 16-20)

**Status:** QUALITY GATE VALIDATION

| Day | Task | Deliverable | Owner |
|-----|------|-------------|-------|
| 16 | Add YAML frontmatter to 6 spec files | Fix docs/spec/*.md files | technical-writer-expert |
| 17 | Write migration script | `scripts/migrate_agents_yaml_to_md.py` | senior-developer |
| 18 | Migrate 18 YAML agents to .md format | `config/agents/*.md` converted | senior-developer |
| 19 | End-to-end pipeline test | Full 4-stage execution with real task | test-engineer |
| 20 | Quality Gate 7 review | All 13 criteria verified | testing-quality-specialist |

**Week 4 Quality Gate:** Full Quality Gate 7 PASS (13/13 criteria)

---

## Sprint Breakdown (Alternative 2-Week Sprints)

### Sprint 1: Foundation + Templates (Weeks 1-2)
- Registry extension (_load_md_agent, _build_agent_definition)
- senior-developer.md proof-of-concept
- Template library (17 files)
- 10 unit tests
- Tests: 10 unit tests at 100% pass

### Sprint 2: Stage Agents + Integration (Weeks 3-4)
- 4 stage agent implementations
- Migration script + 18 YAML to MD conversion
- End-to-end pipeline test
- Quality Gate 7 validation
- Tests: 330+ tests at 100% pass

---

## Dependencies

### Internal Dependencies

| Component | Depends On | Phase | Status |
|-----------|------------|-------|--------|
| Domain Analyzer | `_load_md_agent()` | Phase 5 | IMPLEMENTING |
| Workflow Modeler | Domain Analyzer blueprint | Phase 5 | BLOCKED |
| Loom Builder | Workflow Modeler graph | Phase 5 | BLOCKED |
| Ecosystem Builder | Template library + gap list | Phase 5 | BLOCKED |
| Registry Extension | `_build_agent_definition()` | Phase 5 | IMPLEMENTING |

### External Dependencies

| Dependency | Version | Purpose | Status |
|------------|---------|---------|--------|
| PR #720 (AgentRegistry) | Merged to main | Agent discovery | PENDING MERGE |
| Template Library | N/A | Agent generation scaffolding | IMPLEMENTING |
| `pyyaml` | >=6.0 | YAML frontmatter parsing | AVAILABLE |
| `watchdog` | >=3.0 | Hot-reload for registry | AVAILABLE |

### PR #720 Integration

| Task | Owner | Status | Blocking |
|------|-------|--------|----------|
| Open GitHub discussion on `pipeline:` extension | software-program-manager | TODO | Yes |
| Confirm registry naming convention (AgentRegistry vs AgentDiscovery) | software-program-manager | TODO | Yes |
| Execute rebase after PR #720 merges | senior-developer | TODO | Yes |
| Resolve registry.py conflict (full manual resolution) | senior-developer | TODO | Yes |
| Update `_model_load_lock` to `model_load_lock` | senior-developer | TODO | Yes |

---

## Work Breakdown Structure

### Task ID: PH5-W1-D1 — Apply 8 Spec Edits

**Subject:** Apply targeted edits to agent-ecosystem-design-spec.md

**Description:** Apply the 8 specified edits from the action plan to `docs/spec/agent-ecosystem-design-spec.md`. Each edit is a surgical replacement with exact location specified.

**Acceptance Criteria:**
- Edit 1.1: complexity_range changed to list format [0.3, 1.0] in Section 3.2
- Edit 1.2: complexity_range changed in Section 3.4 example
- Edit 1.3: capabilities comment updated to reference Phase 2 VALID_CAPABILITY_STRINGS
- Edit 1.4: Scope Boundary subsection added to Section 4.4
- Edit 1.5: _load_md_agent() code fixed with utf-8-sig and CRLF normalization
- Edit 1.6: Two unit tests added to Section 6.2 list
- Edit 1.7: Capabilities validation language updated in Section 6.1
- Edit 1.8: Four Open Questions closed with resolutions

**Dependencies:** None
**Estimated Effort:** 2 hours
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W1-D2 — Extract _build_agent_definition()

**Subject:** Extract field-parsing helper from _load_agent()

**Description:** Extract all field-parsing logic from `_load_agent()` into a new `_build_agent_definition()` helper method that both `_load_agent()` and `_load_md_agent()` will call.

**Acceptance Criteria:**
- `_build_agent_definition(data, system_prompt_override)` method exists in registry.py
- Handles both nested (`data["agent"]`) and flat field formats
- Handles complexity_range in both dict and list formats
- `_load_agent()` calls `_build_agent_definition()` (no duplicated logic)
- Existing `_load_agent()` behavior unchanged (backward compatible)

**Dependencies:** PH5-W1-D1 (spec defines the format)
**Estimated Effort:** 3 hours
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W1-D3 — Implement _load_md_agent()

**Subject:** Implement MD agent loader with CRLF/BOM handling

**Description:** Implement `_load_md_agent()` method that parses Markdown files with YAML frontmatter, handling Windows CRLF line endings and UTF-8 BOM transparently.

**Acceptance Criteria:**
- Uses `encoding="utf-8-sig"` to strip BOM
- Normalizes CRLF to LF before string operations
- Correctly splits frontmatter from body using explicit `find()` logic
- Raises `AgentLoadError` for invalid frontmatter
- Calls `_build_agent_definition()` to parse fields
- Returns `AgentDefinition` with non-empty system_prompt

**Dependencies:** PH5-W1-D2 (_build_agent_definition exists)
**Estimated Effort:** 4 hours
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W1-D4 — Extend _load_all_agents()

**Subject:** Add .md file discovery with collision guard

**Description:** Extend `_load_all_agents()` to glob `*.md` files and dispatch to correct loader. Add agent ID collision detection to prevent silent overwrites.

**Acceptance Criteria:**
- Glob includes `*.md` files in addition to `*.yaml` and `*.yml`
- Dispatches to `_load_md_agent()` for `.md` files, `_load_agent()` for others
- Detects ID collision (same agent ID from .yaml and .md)
- Logs warning and skips second file on collision
- Preserves YAML-first loading order (YAML wins during migration)

**Dependencies:** PH5-W1-D3 (_load_md_agent exists)
**Estimated Effort:** 2 hours
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W1-D5 — Write senior-developer.md

**Subject:** Create proof-of-concept MD agent file

**Description:** Write `config/agents/senior-developer.md` exactly as specified in action plan Deliverable 4. This is the proof-of-concept for the entire .md format.

**Acceptance Criteria:**
- Begins with `---` on first line
- Uses list format `complexity_range: [0.3, 1.0]`
- Has non-empty system prompt body after closing `---`
- Contains 5+ tool-call blocks conforming to Section 4 syntax
- Loads via `_load_md_agent()` without errors
- All frontmatter fields parse correctly

**Dependencies:** PH5-W1-D3 (_load_md_agent works)
**Estimated Effort:** 2 hours
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W2-D6 — Write Unit Tests

**Subject:** Create 10 unit tests for _load_md_agent()

**Description:** Write comprehensive unit tests covering all edge cases for the MD agent loader.

**Acceptance Criteria:**
- `test_load_md_agent_minimal_frontmatter` — minimal valid file loads
- `test_load_md_agent_full_frontmatter` — all fields parse correctly
- `test_load_md_agent_no_frontmatter` — raises AgentLoadError
- `test_load_md_agent_missing_required_field` — raises AgentLoadError
- `test_load_md_agent_body_preserves_special_characters` — horizontal rules, Unicode, code blocks preserved
- `test_load_md_agent_crlf_line_endings` — CRLF-encoded file loads
- `test_load_md_agent_bom_prefix` — BOM-prefixed file loads
- `test_load_md_agent_complexity_range_list` — list format parses to (0.3, 1.0)
- `test_load_md_agent_complexity_range_dict` — dict format backward compatible
- `test_registry_discovers_both_yaml_and_md` — both file types discovered

**Dependencies:** PH5-W1-D3 (_load_md_agent implemented)
**Estimated Effort:** 6 hours
**Required Agent:** test-engineer
**Priority:** P1

---

### Task ID: PH5-W2-D7 to D10 — Create Template Library

**Subject:** Create 17-file template library

**Description:** Create the global template library at `/c/Users/antmi/.claude/templates/` with README, 5 agent templates, 5 component templates, 4 pipeline templates, and 2 meta templates.

**Acceptance Criteria:**
- `README.md` with complete variable index and usage guide
- `agents/agent-minimal.md` — required fields only
- `agents/agent-full.md` — all optional fields
- `agents/agent-tool-calling.md` — Section 4 tool-call examples
- `agents/agent-mcp-consumer.md` — MCP-heavy agent
- `agents/agent-pipeline-stage.md` — pipeline stage node
- `components/command.md`, `task.md`, `checklist.md`, `knowledge-base.md`, `utility.md`
- `pipeline/domain-analysis-output.md`, `workflow-model.md`, `loom-topology.md`, `ecosystem-manifest.md`
- `meta/ecosystem-handoff.md`, `agent-stub.yaml`
- All templates have HTML comment header with variable index
- All agent templates use list format for complexity_range
- All tool-call blocks conform to Section 4 syntax

**Dependencies:** PH5-W1-D1 (spec defines format)
**Estimated Effort:** 16 hours (4 hours per day x 4 days)
**Required Agent:** technical-writer-expert
**Priority:** P1

---

### Task ID: PH5-W3-D11 to D14 — Implement Stage Agents

**Subject:** Implement 4 pipeline stage agents

**Description:** Write the 4 stage agents as .md files using the template library. Each agent must have complete tool-call blocks for its stage responsibilities.

**Acceptance Criteria:**
- `domain-analyzer.md` — analyzes task, produces blueprint with handoff section
- `workflow-modeler.md` — reads blueprint, produces execution graph
- `loom-builder.md` — reads graph, produces topology and gap list
- `ecosystem-builder.md` — reads gap list, generates agent files from templates
- All 4 agents load via `_load_md_agent()` without errors
- All tool-call blocks conform to Section 4 syntax
- Each agent has 5+ phases with 2+ tool calls per phase

**Dependencies:** PH5-W2-D10 (template library complete)
**Estimated Effort:** 16 hours (4 hours per agent)
**Required Agent:** senior-developer
**Priority:** P0

---

### Task ID: PH5-W4-D16 — Fix Spec Frontmatter

**Subject:** Add YAML frontmatter to 6 spec files

**Description:** Add Mintlify-required YAML frontmatter to 6 spec files that are missing it.

**Files to Fix:**
- `docs/spec/agent-ui-eval-kpi-reference.md`
- `docs/spec/agent-ui-eval-kpis.md`
- `docs/spec/gaia-loom-architecture.md`
- `docs/spec/nexus-gaia-native-integration-spec.md`
- `docs/spec/pipeline-metrics-competitive-analysis.md`
- `docs/spec/pipeline-metrics-kpi-reference.md`

**Acceptance Criteria:**
- Each file begins with `---\ntitle: <title>\n---\n`
- Titles match existing H1 headings
- All 6 files pass Mintlify build validation

**Dependencies:** None
**Estimated Effort:** 1 hour
**Required Agent:** technical-writer-expert
**Priority:** P2

---

### Task ID: PH5-W4-D17 to D18 — Migrate YAML Agents

**Subject:** Write migration script and convert 18 YAML agents to MD

**Description:** Write a migration script that converts existing YAML agents to .md format with placeholder prompt bodies. Then run it to convert all 18 agents.

**Acceptance Criteria:**
- `scripts/migrate_agents_yaml_to_md.py` exists
- Script reads YAML files from `config/agents/`
- Script writes .md files with valid frontmatter + placeholder body
- Converts `complexity_range` dict to list format
- Does not modify or delete source .yaml files
- All 18 agents converted and load via `_load_md_agent()`

**Dependencies:** PH5-W1-D3 (_load_md_agent works), PH5-W2-D10 (templates exist)
**Estimated Effort:** 8 hours
**Required Agent:** senior-developer
**Priority:** P2

---

### Task ID: PH5-W4-D19 — End-to-End Pipeline Test

**Subject:** Execute full 4-stage pipeline with real task

**Description:** Run a complete pipeline execution: provide a task description to Stage 1, execute all 4 stages, verify at least one generated agent loads correctly.

**Acceptance Criteria:**
- Task description provided (e.g., "Build a task management web app")
- Domain Analyzer produces blueprint with Ecosystem Builder Handoff section
- Workflow Modeler produces execution graph
- Loom Builder produces topology and gap list
- Ecosystem Builder generates at least one .md agent file
- Generated agent loads via `_load_md_agent()` without errors
- Generated agent has valid frontmatter and non-empty system prompt

**Dependencies:** PH5-W3-D14 (all 4 stage agents implemented)
**Estimated Effort:** 6 hours
**Required Agent:** test-engineer
**Priority:** P0

---

### Task ID: PH5-W4-D20 — Quality Gate 7 Validation

**Subject:** Validate all 13 Quality Gate 7 criteria

**Description:** Execute validation tests for all Quality Gate 7 criteria and produce the Phase 5 closeout report.

**Acceptance Criteria:**
- DOMAIN-001: Entity extraction >90% verified
- DOMAIN-002: Boundary detection 100% verified
- DOMAIN-003: Complexity assessment validity >85% verified
- GENERATION-001: Generated code compiles 100% verified
- GENERATION-002: Generated tools functional 100% verified
- GENERATION-003: Generated prompts coherent 100% verified
- ORCHESTRATION-001: Agent selection accuracy >90% verified
- ORCHESTRATION-002: Task distribution efficiency verified
- ORCHESTRATION-003: Result coherence 100% verified
- INTEGRATION-001: E2E pipeline execution PASS verified
- INTEGRATION-002: Generated agents functional PASS verified
- THREAD-007: Thread safety 100+ threads PASS verified
- Phase 5 closeout report written

**Dependencies:** PH5-W4-D19 (E2E test complete)
**Estimated Effort:** 8 hours
**Required Agent:** testing-quality-specialist
**Priority:** P0

---

## Dependency Graph

```
                                    ┌─────────────────────┐
                                    │  PR #720 Merged     │
                                    │  (External Dep)     │
                                    └──────────┬──────────┘
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    MILESTONE 1 — FOUNDATION                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ PH5-W1-D1    │→ │ PH5-W1-D2    │→ │ PH5-W1-D3    │          │
│  │ Spec Edits   │  │ Extract      │  │ _load_md_    │          │
│  │              │  │ _build_      │  │ agent()      │          │
│  │              │  │ agent_def    │  │              │          │
│  └──────────────┘  └──────────────┘  └──────┬───────┘          │
│                                             │                   │
│                              ┌──────────────┴──────────────┐   │
│                              │                             │   │
│                              ▼                             ▼   │
│                       ┌──────────────┐           ┌──────────────┐
│                       │ PH5-W1-D4    │           │ PH5-W1-D5    │
│                       │ Extend       │           │ senior-      │
│                       │ _load_all_   │           │ developer.md │
│                       │ agents()     │           │              │
│                       └──────────────┘           └──────────────┘
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                 MILESTONE 2 — INTEGRATION                        │
│  ┌──────────────┐                    ┌──────────────┐          │
│  │ PH5-W2-D6    │                    │ PH5-W2-D7    │          │
│  │ Unit Tests   │                    │ Template     │          │
│  │ (10 tests)   │                    │ Library      │          │
│  │              │←───────────────────│ (17 files)   │          │
│  └──────────────┘                    └──────────────┘          │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                 MILESTONE 3 — ECOSYSTEM BUILDER                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ PH5-W3-D11   │→ │ PH5-W3-D12   │→ │ PH5-W3-D13   │          │
│  │ Domain       │  │ Workflow     │  │ Loom         │          │
│  │ Analyzer     │  │ Modeler      │  │ Builder      │          │
│  └──────────────┘  └──────────────┘  └──────┬───────┘          │
│                                             │                   │
│                                             ▼                   │
│                                      ┌──────────────┐          │
│                                      │ PH5-W3-D14   │          │
│                                      │ Ecosystem    │          │
│                                      │ Builder      │          │
│                                      └──────────────┘          │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│              MILESTONE 4 — QUALITY GATE 7                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ PH5-W4-D16   │  │ PH5-W4-D17   │  │ PH5-W4-D19   │          │
│  │ Fix Spec     │  │ Migration    │  │ E2E Pipeline │          │
│  │ Frontmatter  │  │ Script       │  │ Test         │          │
│  └──────────────┘  └──────────────┘  └──────┬───────┘          │
│                                             │                   │
│                                             ▼                   │
│                                      ┌──────────────┐          │
│                                      │ PH5-W4-D20   │          │
│                                      │ QG7          │          │
│                                      │ Validation   │          │
│                                      └──────────────┘          │
└──────────────────────────────────────────────────────────────────┘
```

---

## Risk Register

### High-Probability / High-Impact Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R5.1 | Template tool-call blocks inconsistent with Section 4 spec | High | High | quality-reviewer must cross-check every tool-call against Section 4 before Milestone 1 acceptance | senior-developer, quality-reviewer |
| R5.2 | _load_md_agent() split logic fails on edge-case files | Medium | High | Unit tests must include body with `---` horizontal rule, body with no trailing newline | test-engineer |
| R5.3 | Registry naming collision with PR #720 | High | Medium | Rename our registry to `PipelineAgentRegistry` in `pipeline/agent_registry.py` before rebase | senior-developer |
| R5.4 | Agent ID collision during migration (both .yaml and .md exist) | Medium | Medium | Collision guard logs warning and skips second file; document in migration guide | senior-developer |
| R5.5 | Milestone 1 scope too large for single iteration | Medium | Medium | Priority order: P0 (Tasks 1-5) > P1 (Task 6) > P2 (Task 7); defer P2 if needed | software-program-manager |

### Medium-Probability / Medium-Impact Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R5.6 | Complexity vocabulary drift before VALID_CAPABILITY_STRINGS | High | Medium | Extract and normalize capabilities from 18 YAML files before migration; document in capabilities.py comment | senior-developer |
| R5.7 | senior-developer.md prompt body deviates from Deliverable 4 | Low | Medium | Write verbatim from action plan; report discrepancies as scope change, not silent fixes | senior-developer |
| R5.8 | Template library not accessible from CI/CD | Medium | Low | Template path configurable via `GAIA_TEMPLATE_LIBRARY_PATH` env var; document for Milestone 3 | senior-developer |
| R5.9 | CRLF/BOM fix insufficient for all Windows edge cases | Low | High | Test fixtures must include Notepad-saved files, BOM-prefixed files, mixed line endings | test-engineer |

### Low-Probability / Low-Impact Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R5.10 | `.values()` ordering assumption breaks legacy dict format | Low | High | Use explicit `raw.get("min")` and `raw.get("max")` instead of `.values()` | senior-developer |
| R5.11 | Template staleness as spec evolves | High | Medium | Section 9.5 documents maintenance obligation; same-commit template updates required | technical-writer-expert |
| R5.12 | Stage agents require more iteration budget than allocated | Medium | Medium | Priority order: Domain Analyzer > Workflow Modeler > Loom Builder > Ecosystem Builder | software-program-manager |

---

## Success Metrics Dashboard Template

### Phase 5 Progress Dashboard

```markdown
## Phase 5 Progress — Week X of 4

### Milestone Status

| Milestone | Status | Completion | Quality Gate |
|-----------|--------|------------|--------------|
| Milestone 1 — Foundation | IN PROGRESS / COMPLETE | X/5 tasks | PENDING / PASS |
| Milestone 2 — Integration | NOT STARTED / IN PROGRESS / COMPLETE | X/5 tasks | PENDING / PASS |
| Milestone 3 — Ecosystem Builder | NOT STARTED / IN PROGRESS / COMPLETE | X/4 tasks | PENDING / PASS |
| Milestone 4 — Quality Gate 7 | NOT STARTED / IN PROGRESS / COMPLETE | X/5 tasks | PENDING / PASS |

### Quality Gate 7 Criteria Status

| Criteria | Target | Current | Status |
|----------|--------|---------|--------|
| DOMAIN-001 | >90% | TBD | NOT TESTED |
| DOMAIN-002 | 100% | TBD | NOT TESTED |
| DOMAIN-003 | >85% | TBD | NOT TESTED |
| GENERATION-001 | 100% | TBD | NOT TESTED |
| GENERATION-002 | 100% | TBD | NOT TESTED |
| GENERATION-003 | 100% | TBD | NOT TESTED |
| ORCHESTRATION-001 | >90% | TBD | NOT TESTED |
| ORCHESTRATION-002 | <10% idle | TBD | NOT TESTED |
| ORCHESTRATION-003 | 100% | TBD | NOT TESTED |
| INTEGRATION-001 | PASS | TBD | NOT TESTED |
| INTEGRATION-002 | PASS | TBD | NOT TESTED |
| THREAD-007 | 100+ threads | TBD | NOT TESTED |

### Task Completion Summary

| Priority | Total | Complete | In Progress | Not Started |
|----------|-------|----------|-------------|-------------|
| P0 | 12 | X | X | X |
| P1 | 4 | X | X | X |
| P2 | 4 | X | X | X |

### Risk Status

| Risk ID | Status | Notes |
|---------|--------|-------|
| R5.1 | ACTIVE / MITIGATED / REALIZED | ... |
| R5.2 | ACTIVE / MITIGATED / REALIZED | ... |

### Blockers

- [List any blocking issues]

### Next Actions

- [List next actions for each workstream]
```

---

## Resource Allocation

### Agent Assignments by Workstream

| Workstream | Primary Agent | Support Agent | Reviewer |
|------------|---------------|---------------|----------|
| Registry Extension | senior-developer | - | quality-reviewer |
| Template Library | technical-writer-expert | senior-developer | quality-reviewer |
| Stage Agents (1-4) | senior-developer | gaia-agent-builder | quality-reviewer |
| Unit Tests | test-engineer | - | testing-quality-specialist |
| Migration Script | senior-developer | - | quality-reviewer |
| E2E Pipeline Test | test-engineer | senior-developer | testing-quality-specialist |
| Quality Gate 7 | testing-quality-specialist | test-engineer | quality-reviewer |
| Spec Frontmatter Fix | technical-writer-expert | - | quality-reviewer |
| PR #720 Coordination | software-program-manager | - | kovtcharov-amd |

---

## PR #720 Coordination Tasks

### Pre-Merge Tasks (P0 — Before PR #720 merges)

| Task ID | Task | Owner | Status | Blocking |
|---------|------|-------|--------|----------|
| PR720-COORD-1 | Open GitHub discussion on `pipeline:` extension to AgentManifest | software-program-manager | TODO | Yes |
| PR720-COORD-2 | Confirm registry naming convention (AgentRegistry vs AgentDiscovery) | software-program-manager | TODO | Yes |

### Post-Merge Tasks (P1 — Immediately after PR #720 merges)

| Task ID | Task | Owner | Status | Blocking |
|---------|------|-------|--------|----------|
| PR720-COORD-3 | Execute rebase onto updated main | senior-developer | TODO | Yes |
| PR720-COORD-4 | Resolve registry.py conflict (full manual resolution) | senior-developer | TODO | Yes |
| PR720-COORD-5 | Update `_model_load_lock` to `model_load_lock` | senior-developer | TODO | Yes |
| PR720-COORD-6 | Rename our registry to `PipelineAgentRegistry` | senior-developer | TODO | Yes |

### Post-Rebase Tasks (P2 — Within one sprint)

| Task ID | Task | Owner | Status | Blocking |
|---------|------|-------|--------|----------|
| PR720-COORD-7 | Implement AgentOrchestrator bridge | senior-developer | TODO | No |
| PR720-COORD-8 | Wire resilience primitives into routing_engine.py | senior-developer | TODO | No |
| PR720-COORD-9 | Standardize 18 YAML capabilities against vocabulary | senior-developer | TODO | No |

---

## Migration Notes for Users

### Upgrading from Phase 4 to Phase 5

**Compatibility:** Phase 5 is **backward compatible** with Phase 4. All existing agent definitions and registry functionality remain functional.

### New Agent File Format

Phase 5 introduces Markdown agent definitions with YAML frontmatter:

```markdown
---
id: agent-id
name: Agent Name
version: 1.0.0
category: category
description: Agent description
triggers:
  keywords: [keyword1, keyword2]
  phases: [PHASE1, PHASE2]
  complexity_range: [0.3, 1.0]  # List format only
capabilities:
  - capability1
  - capability2
tools:
  - tool1
  - tool2
---

# Agent System Prompt Body

The Markdown body after the closing `---` is the agent's system prompt.
```

### Migration Script

```bash
# Convert all YAML agents to MD format
python scripts/migrate_agents_yaml_to_md.py
```

### Template Library Usage

The template library at `/c/Users/antmi/.claude/templates/` provides scaffolding for agent generation. Set `GAIA_TEMPLATE_LIBRARY_PATH` to override the default location.

---

## Document History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-04-07 | Initial Phase 5 implementation plan | software-program-manager |

---

## Contact

**Phase 5 Owner:** software-program-manager
**Technical Lead:** senior-developer
**Quality Lead:** testing-quality-specialist
**Documentation Lead:** technical-writer-expert
**Escalation:** @kovtcharov-amd

---

**Document Status:** READY FOR KICKOFF
**Next Action:** Begin Week 1 Day 1 — Apply 8 spec edits
**Quality Gate 7 Target:** 13/13 criteria PASS, 340+ tests at 100% pass rate, 85%+ coverage

---

**END OF PHASE 5 IMPLEMENTATION PLAN**
