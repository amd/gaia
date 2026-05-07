# Commit Classification — feature/pipeline-orchestration-v1

> 132 commits from `origin/main` → `feature/pipeline-orchestration-v1`
> Generated: 2026-05-07

## Summary

| Category | Count | % |
|----------|-------|---|
| **NEW FEATURES** | 52 | 39% |
| **CODEBASE UPDATES (fixes)** | 36 | 27% |
| **DOCUMENTATION** | 24 | 18% |
| **SECURITY** | 3 | 2% |
| **TESTS** | 6 | 5% |
| **CHORE / MAINTENANCE** | 7 | 5% |
| **RELEASES** | 3 | 2% |
| **MERGES** | 1 | 2% |

---

## A. NEW FEATURES (52 commits) — New capabilities, modules, systems

| SHA | Title | What's New |
|-----|-------|-----------|
| efb1ca7 | feat(pipeline): GAIA pipeline orchestration engine P1-P6 | Foundational pipeline orchestration engine |
| eb0a838 | feat(orchestration): add Phase 1 core orchestration kernel (89 tests) | Orchestration kernel, models, state machine |
| dd1d314 | feat(orchestration): add Phase 2A ProjectSupervisor hierarchy (56 tests) | ProjectSupervisor agent coordination |
| dc02956 | feat(orchestration): Phase 2B supervisor hierarchy — GitSupervisor + Registry | GitSupervisor + supervisor registry |
| 6f95323 | feat(orchestration): Phase 3 automation hooks — "Hooks Recalculate" | Modular automation hooks (git, PR, rollback) |
| e0ed934 | feat(orchestration): Phase 4 parallel execution, conflict detection, rollback, worktree lifecycle | Parallel engine with conflict detection |
| 97edfd7 | feat(pipeline): wire PipelineEngine events to SSE stream, fix critical drain bug | SSE event wiring for pipeline engine |
| d3951f8 | feat(pipeline): add artifact provenance tracking in PipelineSnapshot | Artifact provenance tracking system |
| 47c0c0c | feat(pipeline): add Sprint 1-2 integration tests (151 tests, 88% coverage) | Integration test suite for pipeline engine |
| 5a37360 | fix(pipeline): add resilience APIs and fix 28 integration tests | Resilience APIs (circuit breaker, retry) |
| 5bd6ef8 | feat(ui): orchestrator UI visibility layer — REST API + SSE streaming + control endpoints | Orchestrator REST API + SSE endpoints |
| d187907 | feat(pipeline): recursive pipeline SSE streaming and agent registry source editing | Recursive SSE streaming + registry editing |
| 3838a8a | feat(ui): add visual drag-and-drop pipeline canvas | Visual drag-and-drop pipeline canvas |
| ef98904 | feat(ui): add supervisor agents, decision gates, loop blocks, and workspace tools to Pipeline Canvas | Canvas: supervisors, gates, loops, workspace |
| 9a85250 | feat(ui): add execution history, replay, and template versioning (Tier 3) | Execution history, replay, template versioning |
| 856f1b2 | feat(ui): complete Tier 3 pipeline canvas - template marketplace, performance dashboard, execution history | Tier 3: template marketplace, perf dashboard |
| 55b890d | feat(ui): add multiple independent loops, custom agent selection, free supervisor placement | Multi-loop, custom agent selection UI |
| c27e42e | feat(component-registry): add component framework registry UI and integration tests | Component registry UI |
| 33686dd | feat(ui): Add Pipeline Runner page with SSE streaming execution UI | Pipeline Runner page with SSE |
| f22f48a | feat(webui): display agent ecosystem in Pipeline Runner | Agent ecosystem display in Pipeline Runner |
| 214c314 | feat(agents): add 5 new supervisor agents with embedded system prompts | 5 supervisor agent implementations |
| 520bea3 | feat(agents): add component framework tools to Agent base class | Component framework tools in Agent base |
| 57ee63d | feat(component-framework): implement template system with loader utility | Component template system + loader |
| 8d6ffdd | feat(pipeline): add DomainAnalyzer stage with component integration | DomainAnalyzer pipeline stage |
| a32187c | feat(pipeline): add WorkflowModeler stage for workflow pattern selection | WorkflowModeler pipeline stage |
| 8dd22c1 | feat(pipeline): add LoomBuilder stage for agent execution graph construction | LoomBuilder pipeline stage |
| 0c5f294 | feat(pipeline): add PipelineExecutor stage for agent orchestration execution | PipelineExecutor pipeline stage |
| fa3ef98 | feat(pipeline): add autonomous agent spawning with GapDetector | Auto-spawn with GapDetector |
| 41ee396 | feat(phase5): Complete five-stage auto-spawn pipeline implementation | Full five-stage auto-spawn pipeline |
| e952716 | feat(phase5): Complete component-framework templates and tool calling docs | Component framework templates |
| 8b05805 | feat(health): add Phase 4 Week 1 Health Monitoring module | Health monitoring probes |
| 84ed269 | feat(resilience): add Phase 4 Week 2 Resilience Patterns | Resilience patterns (bulkhead, circuit breaker, retry) |
| 4c02e45 | feat: add Phase 4 Week 3 Data Protection + Performance Profiling | Data protection + performance profiling |
| d8f0269 | feat(phase3): Sprint 1 - Modular Architecture Core Implementation | Modular architecture core |
| 505d22f | feat(phase3): Sprint 2 - Dependency Injection + Performance | Dependency injection + performance |
| 64db788 | feat(phase3): Sprint 3 - Caching + Enterprise Config | Caching + enterprise configuration |
| c25982b | feat(phase3): Sprint 4 - Observability + API Standardization | Observability + API standardization |
| 32f4cf4 | feat(baibel): Complete Phase 0, 1, 2 - BAIBEL Integration Framework | BAIBEL integration framework |
| 1fbffb9 | feat(pipeline): add artifact extractor for code file output and root cause docs | Artifact extractor for code files |
| b533669 | feat(pipeline): implement RC#2 tool package and fix RC#6/RC#8 in ConfigurableAgent | RC#2 tool package |
| eed48d2 | feat(pipeline): propagate agent LLM outputs to state machine and improve output visibility | LLM output propagation to state machine |
| 8cce2d9 | feat(pipeline): add demo scripts, Lemonade integration, and fix stub mode | Demo scripts + Lemonade integration |
| 7832c7e | feat(pipeline): add model_id support across all pipeline layers | model_id support across pipeline |
| 7ed2db3 | feat(cpp): SSE streaming response support for C++ agent framework (#518) | C++ SSE streaming |
| 9c4101d | feat(cpp): performance benchmarks and binary size tracking (#519) | C++ benchmarks |
| 878a976 | feat(cpp): runtime configuration and dynamic reconfiguration (#531) | C++ runtime config |
| 5d167c4 | feat(pipeline): complete metrics dashboard, template management, and comprehensive testing | Metrics dashboard + template management |
| 31de02f | feat(eval): integrate pipeline performance metrics with agent eval framework (Phase 2) | Pipeline eval metrics integration |
| 780a711 | feat: Lemonade version mismatch warning, eval perf tracking, MCP stats (#637) | Lemonade warning + eval perf + MCP stats |
| b2ace80 | Add GAIA Chat UI: privacy-first desktop chat with document Q&A (#428) | GAIA Chat UI desktop app |
| c72e6d9 | feat: Agent UI eval benchmark framework with gaia eval agent command (#607) | Agent UI eval benchmark framework |
| 2630b38 | feat(pipeline): Add PhaseContract, AuditLogger, and DefectRemediationTracker | PhaseContract + AuditLogger + DefectRemediation |

---

## B. CODEBASE UPDATES / FIXES (36 commits) — Modifications to existing GAIA code

| SHA | Title | What Changed |
|-----|-------|-------------|
| 4015bb2 | Fix Lemonade v10 system-info device key compatibility (#548) | Lemonade v10 device key fix |
| 25c6d25 | Agent UI: terminal animations, pixelated cursor, and docs fixes (#568) | Agent UI terminal/cursor rendering |
| 8a6452f | Fix LRU eviction silent failure allowing unbounded memory growth (#449) (#567) | LRU eviction fix in memory management |
| cc90935 | Fix Agent UI Round 5: hide post-tool thinking, FileListView, text spacing (#566) | Agent UI rendering fixes |
| 95b304f | Fix Agent UI guardrails, rendering, LRU eviction, and Windows paths (#604) | Agent UI multi-fix (guardrails, rendering, paths) |
| 859058f | fix(ui): Improve PipelineRunner accessibility and state management | PipelineRunner accessibility + state |
| 1761d70 | fix(ui): Resolve TypeScript errors in PipelineRunner and API service | PipelineRunner TypeScript errors |
| 4faa22e | fix(webui): resolve double /api prefix in pipeline API calls | API double-prefix bug fix |
| cf3469f | docs: update phase 5 status - runtime verified, all endpoints functional | Phase 5 runtime verification |
| 0ab5554 | fix(webui): resolve TypeScript build errors in metrics and templates | WebUI TypeScript build fix |
| cea803a | fix(ui): resolve canvas TypeScript errors and React setState warning | Canvas TypeScript + React warnings |
| 1ffd7a6 | fix(pipeline): resolve UI wiring for supervisor/loop canvas nodes, decision gates, and workspace visibility | Canvas UI wiring fix |
| 9bc85ec | fix(pipeline): resolve final quality review issues -- event loops, orchestrator | Event loops + orchestrator fixes |
| 0ed82d4 | fix(pipeline): consolidate event loops in ThreadPoolExecutor threads | ThreadPoolExecutor event loop consolidation |
| 961c7d5 | fix(pipeline): fix canvas loop path -- artifact propagation and state safety | Canvas loop path + state safety |
| 574d142 | fix(pipeline): resolve testing validation bugs in canvas wiring and quality scoring | Canvas testing + quality scoring bugs |
| 957a7cb | fix(pipeline): wire canvas config, bridge quality scoring, enable resilience | Canvas config + resilience wiring |
| 71d5d48 | fix(pipeline): resolve all hard-stop runtime bugs and wire gaia pipeline CLI | Pipeline CLI wiring + runtime bugs |
| 242e380 | fix(pipeline): fix execute_tool dispatch bugs blocking real pipeline runs | execute_tool dispatch bug |
| fa8b17d | fix(resilience): consolidate ResilienceError, remove duplicate method | ResilienceError consolidation |
| ee43966 | fix(pipeline): add SEC-003 path traversal protection in artifact_extractor.py | Path traversal protection |
| 03d15bd | fix(pipeline): remove PipelineIsolation waste and fix agent ID collisions | PipelineIsolation cleanup + ID collision fix |
| ec86362 | fix(agents): resolve AgentDefinition/AgentConstraints dataclass mismatch and remove shadow module | AgentDefinition dataclass fix |
| 2d08088 | fix: reduce system prompt 78% to fix Qwen3.5 timeouts + MCP runtime status (#609) (#617) | System prompt reduction |
| 7781ef9 | fix(phase3): Resolve Phase 3 Sprint 4 integration test failures | Phase 3 test failures |
| af652d9 | fix: RAG indexing guards, gaia init pip extras, and docs update (#605) | RAG indexing guards |
| b7a97e6 | Restore changes reverted by accidental PR #566 merge (#564, #565, #568) (#608) | Restore reverted changes |
| 54c5499 | feat(phase5): milestone 3 pipeline agents, Agent UI rendering fixes, and ecosystem docs | Agent UI rendering fixes (milestone 3) |
| 20beb54 | feat: Add ConfigurableAgent with tool isolation and DefectRouter | ConfigurableAgent with tool isolation |
| e0e5695 | fix: isolate MCP unit tests from real ~/.gaia/mcp_servers.json (#658) | MCP test isolation |
| bb010a0 | fix: build Agent UI frontend in gaia init and fix doc prerequisites (#657) | Agent UI build fix in gaia init |
| 83a4db1 | fix: switch npm publish to OIDC trusted publishing (#638) | npm OIDC publish switch |
| 776dc34 | fix: resolve merge-queue-notify phantom failures (#640) | Merge-queue-notify CI fix |
| 334b011 | fix: remove registry-url to enable OIDC trusted publishing (#639) | OIDC registry-url removal |
| 4fe0441 | fix: upgrade npm to 11.5.1+ for OIDC trusted publishing (#683) | npm upgrade for OIDC |
| b19d812 | fix: bump webui package.json version to 0.17.1 (#682) | WebUI version bump |

---

## C. DOCUMENTATION (24 commits) — Docs-only changes (no code)

| SHA | Title | Documentation |
|-----|-------|-------------|
| 07b0e88 | docs: generate PDF bundle of all 70 docs pages from branch | PDF bundle generator script |
| 8772238 | docs: add orchestration user guide with 24 screenshots | Orchestration user guide (1826 lines) |
| 8522e0b | docs: update phase 5 - agent ecosystem display added to Pipeline Runner | Phase 5 agent ecosystem docs |
| c9abc59 | docs: Final documentation coherence fixes for Phase 5 merge | Phase 5 docs coherence |
| 9106a72 | docs: add pipeline canvas user guide and SDK reference | Pipeline Canvas user guide |
| 7c3a6a4 | docs: update implementation tracker with Tier 3 completion details | Tier 3 tracker update |
| b1a15ec | docs: update pipeline canvas guide with History tab and execution history | Canvas guide History tab update |
| 3ce237c | docs: update pipeline canvas implementation tracker with Tier 1-2 completion | Tier 1-2 tracker update |
| 76675ea | docs: archive 62 historical documents and clean up documentation debt | Docs archival + cleanup |
| e28a922 | docs: Resolve Open Item 5 - Update design spec coherence for Phase 5/6 | Design spec coherence |
| 52df806 | docs: update matrix for Phase 6 pull (984 files, 74 commits) | Phase 6 matrix update |
| 49b6704 | docs: update matrix for Phase 6 pull (984 files, 73 commits) | Phase 6 matrix update |
| 6f839a6 | docs: update matrix and design docs for Phase 5 pull (970 files, 71 commits) | Phase 5 matrix + design docs |
| 5c52eb8 | docs: add PR #606 integration analysis for feature/pipeline-orchestration-v1 | PR #606 analysis |
| 078739b | docs: add PR #720 integration analysis for feature/pipeline-orchestration-v1 | PR #720 analysis |
| 08b93eb | docs: add agent ecosystem design spec, action plan, and senior-dev work order | Agent ecosystem design spec |
| 79b1861 | docs: add branch change matrix for feature/pipeline-orchestration-v1 | Branch change matrix |
| d794360 | docs: correct BAIBEL phase status and open items in branch-change-matrix | BAIBEL phase status correction |
| dc4ddda | docs: Add BAIBEL-GAIA Integration Master Specification | BAIBEL master spec |
| daf21f9 | docs(spec): add KPI references, eval metrics, and GAIA Loom architecture specs | KPI + eval + Loom specs |
| 85b1f55 | docs: Add Phase 3 Closeout Report - All 4 Sprints Complete | Phase 3 closeout report |
| 82a6d42 | docs: add Phase 4 closeout report and update roadmap | Phase 4 closeout + roadmap |
| 4345b92 | docs: Add PR description for pipeline orchestration feature | PR description doc |
| 2fd4a80 | docs: fix v0.17.0 release notes — npm install, gaia-ui CLI (#636) | Release notes fix |

---

## D. SECURITY (3 commits) — Security patches

| SHA | Title | Vulnerability |
|-----|-------|-------------|
| 8c2d24a | security: fix TOCTOU race condition in document upload endpoint (#448) (#564) | TOCTOU race condition (SEC-001) |
| 3df90ff | Add tool execution guardrails with confirmation popup (#438) (#565) | Tool execution guardrails |
| 0702252 | fix(security): resolve EtherREPL P0/P1 vulnerabilities (SEC-001 through SEC-003) | EtherREPL P0/P1 (SEC-001, SEC-002, SEC-003) |

---

## E. TESTS (6 commits) — Test-only changes (no production code)

| SHA | Title | Coverage |
|-----|-------|----------|
| b3d707e | test(orchestration): add 7 edge-case tests for parallel execution engine | Semaphore, conflict, rollback, worktree |
| c3ccc4f | test(quality): add 35 unit tests for supervisor agent decisions | Supervisor decision coverage |
| f57e5ba | test(phase5): Add Quality Gate 7 validation tests and report | Quality Gate 7 e2e |
| 3b6ebe6 | test(pipeline): add SSE endpoint lock release and JSON serialization tests | SSE lock + JSON serialization |
| 0ae23c9 | test: fix E2E pipeline integration timeout after Session-2 changes | E2E timeout fix |
| 47c0c0c | feat(pipeline): add Sprint 1-2 integration tests (151 tests, 88% coverage) | 151 integration tests |

---

## F. CHORE / MAINTENANCE (7 commits)

| SHA | Title | Action |
|-----|-------|--------|
| 5931d85 | chore: minor fixes and updates | Minor cleanup |
| d14e3fe | chore: remove .claude/ from git tracking and update .gitignore | .gitignore update |
| ad4f7c6 | chore: add runtime artifact exclusions, untrack chroma DB | Chroma DB untracking |
| 375091e | chore: add __version__.py from pipeline proposal | Version file |
| c290ed7 | feat(pipeline): add missing metrics, agents/definitions, and test modules | Missing modules |
| eff99b6 | Merge remote-tracking branch 'upstream/main' into feature/pipeline-orchestration-v1 | Upstream merge |
| 7e7ff14 | Merge branch 'amd:main' into feature/pipeline-orchestration-v1 | Main merge |

---

## G. RELEASES (3 commits)

| SHA | Title | Version |
|-----|-------|---------|
| bae3a62 | docs(releases): add missing PRs to v0.16.1 release notes (#589) | v0.16.1 docs |
| f7e688e | Release v0.17.0 (#626) | v0.17.0 |
| bc26a31 | Release v0.17.1 (#681) | v0.17.1 |

---

## Quick Reference: Features vs Codebase Updates

### What's entirely NEW (52 commits → new modules/directories):
- `src/gaia/orchestration/` — Orchestration kernel, supervisors, hooks, parallel engine
- `src/gaia/pipeline/` — Pipeline engine, 5 stages (DomainAnalyzer → PipelineExecutor)
- `src/gaia/ui/routers/orchestrator.py` — Orchestrator REST API
- Pipeline Canvas UI — drag-and-drop visual pipeline builder
- Pipeline Runner UI — SSE streaming execution page
- Component Framework — template system, loader, registry
- Supervisor Agents — 5 agents with embedded prompts
- Auto-Spawn Pipeline — GapDetector, 5-stage pipeline
- C++ Framework — SSE streaming, benchmarks, runtime config
- BAIBEL Integration — Phases 0, 1, 2
- Phase 3 Modular Architecture — 4 sprints (DI, caching, observability)
- Phase 4 Health/Resilience — Health monitoring, circuit breaker, data protection
- Chat UI Desktop App — privacy-first document Q&A
- Eval Benchmark Framework — agent UI testing

### What modifies existing GAIA code (36 commits → fixes to existing files):
- `src/gaia/agents/` — AgentDefinition fix, component tools, ConfigurableAgent
- `src/gaia/apps/webui/` — TypeScript fixes, accessibility, API prefix
- `src/gaia/llm/` — Lemonade compatibility, version warnings
- `src/gaia/rag/` — Indexing guards
- `src/gaia/ui/` — LRU eviction, guardrails, rendering, terminal
- `src/gaia/mcp/` — Test isolation
- `src/gaia/installer/` — gaia init build fix
- `.github/workflows/` — CI/CD fixes (npm OIDC, merge-queue)
- `setup.py`, `src/gaia/cli.py` — CLI wiring, system prompt reduction
- `package.json` — Version bumps
