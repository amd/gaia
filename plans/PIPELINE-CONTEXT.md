# PIPELINE CONTEXT — Verifiable Code Changes

> Fork: antmikinka/gaia | Branch: feature/pipeline-orchestration-v1
> 132 commits ported into 110 atomic branches | 164 GitHub issues (#50-#121, #162-#253)

---

## Inference Layer Architecture

- `src/gaia/chat/sdk.py` lines 235 and 323 call `llm_client.chat()` — a method that does not exist on any LLM client class. Verified by tracing through `src/gaia/llm/lemonade_client.py` which provides `chat_completions()` at line 1137 (POSTs to `/chat/completions` with messages array). Created `pr-fix-agent-sdk-inference` branch (PR #255) replacing broken `chat()` calls with direct `chat_completions()` HTTP endpoint.

- `src/gaia/llm/lemonade_client.py` (SHA 780a711, #171) — added Lemonade version mismatch warning at startup, surfacing incompatibilities before inference failures.

## Pipeline Orchestration

- `src/gaia/orchestration/` directory created from scratch (SHA eb0a838, #104): engine (583 lines), models (603 lines), adapters (322 lines), base hooks (192 lines), 89 tests (1678 lines across two test files), plus 5 phase reports. Replaced ad-hoc agent chaining with deterministic workflow management including stage registration, lifecycle callbacks, and concurrent execution.

- Five-stage auto-spawn pipeline (SHA 41ee396, #213): DomainAnalyzer -> GapDetector -> LoomBuilder -> PipelineExecutor -> WorkflowModeler. Pipeline inspects requirements, identifies missing stages, provisions agents automatically. Includes state flow spec (712 lines), code review feedback spec (554 lines), unified capability model spec (434 lines).

- `gaia pipeline` CLI stub wired through `src/gaia/cli.py` and `setup.py` (SHA 969eefe, #185). Includes documentation: pipeline.mdx (531 lines), pipeline-engine.mdx (346 lines), pipeline-demo-plan-v2.md (1095 lines), example pipeline scripts, and smoke tests.

- REST API (625 lines) and SSE streaming endpoints in `src/gaia/ui/routers/orchestrator.py` for real-time pipeline monitoring from Agent UI (SHA 5bd6ef8, #210). Exposes objective management, state transitions, and execution history. 598 lines of API tests.

- Phase 1-6 orchestration engine (SHA efb1ca7, #180): core execution loop, error handling, stage transitions. Foundation for all subsequent pipeline work.

- DI container (770 lines), adapter pattern (545 lines), async utilities (703 lines), connection pooling (787 lines) in Phase 3 Sprint 2 (SHA 505d22f, #198). Production-scale dependency injection for pipeline stages with comprehensive test coverage.

- Parallel execution engine (873 lines, SHA e0ed934, #207): conflict detection for concurrent resource access, rollback mechanisms for failed parallel branches, git worktree lifecycle management, 1642 lines of tests.

- Modular architecture core: capabilities model (417 lines), executor engine (649 lines), plugin system (790 lines), profile management (508 lines) in Phase 3 Sprint 1 (SHA d8f0269, #182).

- Caching system with disk cache, LRU cache, TTL management, enterprise configuration with secrets manager and validators across 7 cache files and 6 config files (SHA 64db788, #206).

- Observability module with metrics, logging, tracing, Prometheus exporter, structured logging formatter, API versioning, deprecation management, OpenAPI specification (SHA c25982b, #208).

## Pipeline UI

- Drag-and-drop visual Pipeline Canvas (SHA 3838a8a, #220): AgentNode.tsx (draggable agents), AgentPalette.tsx (component selection), PipelineCanvas.tsx (main view), PipelineRunner.tsx (execution controls), StageZone.tsx (stage organization), pipelineCanvasStore.ts (state management).

- Pipeline Runner page with SSE streaming (SHA 33686dd, #215): PipelineRunner.tsx, PipelineRunner.css, App.tsx navigation integration, Sidebar.tsx entry. Real-time stage execution monitoring with per-stage status updates.

- Recursive pipeline SSE streaming (SHA d187907, #235): nested sub-pipeline event streaming, AgentRegistry.tsx source editing UI, backend engine.py and orchestrator.py updates for recursive execution.

- Double `/api` prefix bug fix in api.ts (SHA 4faa22e, #241): caused 404 errors on all pipeline API calls. Restored functionality for Pipeline Runner, Canvas, Metrics Dashboard, Template Marketplace.

- TypeScript build fixes across 5 metrics/template components (SHA 0ab5554, #239): MetricSummaryCards.tsx, MetricsDashboard.tsx, PhaseTimingChart.tsx, QualityOverTimeChart.tsx, TemplateEditorDialog.tsx, plus tsconfig.json and metricsStore.ts fixes.

- PipelineRunner TypeScript fix (SHA 1761d70, #240): PipelineRunner.tsx errors, MetricsDashboard.test.tsx type mismatches, API service type definitions.

- Canvas TypeScript fix (SHA cea803a, #221): AgentPalette.tsx and PipelineCanvas.tsx type errors, React setState anti-pattern in pipelineCanvasStore.ts.

- Execution history and replay (SHA 9a85250, #231): ExecutionHistory.tsx (230 lines), pipeline replay controls in PipelineRunner, template versioning, backend router (418 lines).

- Tier 3 canvas completion (SHA 856f1b2, #234): TemplateMarketplace.tsx, PerformanceDashboard, ExecutionHistory.tsx, VersionDiff.tsx, VersionHistory.tsx, store/type/backend/schema updates.

- Supervisor and loop nodes added to canvas (SHA ef98904, #227): DecisionGate.tsx, LoopBlock.tsx, SupervisorNode.tsx, AgentPalette.tsx updates, canvas CSS and store.

- Multiple independent loops (SHA 55b890d, #229): LoopBlock.tsx, custom agent selection per loop, free supervisor placement, canvas store/types/backend router/template/template service updates.

- Component Registry UI (SHA c27e42e, #233): ComponentFileModal.tsx, ComponentRegistry.css, 429-line user guide, 1109 lines of integration tests.

- PipelineRunner accessibility (SHA 859058f, #243): ARIA attributes for screen reader support, keyboard navigation, state synchronization fixes for WCAG 2.1 AA compliance.

## Agent System

- `src/gaia/agents/base/agent.py` — AgentSDK rename (ChatSDK -> AgentSDK, ChatConfig -> AgentConfig) with backwards aliases.

- Component framework tools added to Agent base class (254 lines in agent.py, SHA 520bea3, #95): template loading, frontmatter parsing, component utilities inherited by all agents.

- AgentDefinition/AgentConstraints dataclass mismatch resolved (SHA ec86362, #189): removed shadow module causing import conflicts in agent definitions and base modules.

- ConfigurableAgent with tool isolation and DefectRouter (SHA 20beb54): clean tool separation between agents, pipeline defect management routing.

- System prompt reduced by 78% (SHA 2d08088, #188): fixed Qwen3.5 timeout issues, updated MCP runtime status handling.

- 6 supervisor agent configurations (code, performance, planning, quality, security, testing) with embedded system prompts in both .md and .yaml formats in config/agents/ (SHA 214c314, #187).

- RC#2 tool package: code operations (164 lines), file operations (137 lines), shell operations (97 lines) (SHA b533669, #165).

- Phase 5 milestone 3: 20 agent configurations migrated from YAML to MD format, capability model (agents/registry.py, capabilities.py), Agent UI rendering fix for pipeline agents (SHA 54c5499, #244).

## Health, Resilience, Data Protection

- Health monitoring module (SHA 8b05805, #98): health checker (870 lines), health models (706 lines), health probes (1110 lines), 1848 lines of tests across checker/model/probe files.

- Resilience patterns (SHA 84ed269, #99): bulkhead isolation (284 lines), circuit breaker (344 lines), retry strategies (367 lines), 1826 lines of tests across 3 test files.

- Data protection module (814 lines) and performance profiler (899 lines) with profiler tests (873 lines) and data protection tests (766 lines) (SHA 4c02e45, #100).

- ResilienceError consolidated into dedicated errors.py module (SHA fa8b17d, #200): removed duplicate error handling from bulkhead, circuit breaker, retry modules.

- Resilience APIs (bulkhead, circuit breaker, retry) wired to pipeline orchestrator, 28 failing integration tests fixed (SHA 5a37360, #202).

## Security

- TOCTOU race condition fixed in document upload endpoint (SHA 8c2d24a, #166): atomic check-and-use operations preventing race condition exploitation.

- EtherREPL P0/P1 vulnerabilities resolved (SHA 0702252, #199): SEC-001 code injection, SEC-002 sandbox escape, SEC-003 path traversal. Changes to ether_repl.py (1161 lines), security module, component loader, 513 lines of security tests.

- SEC-003 path traversal protection added to artifact_extractor.py (SHA ee43966, #226): directory escape validation, symlink-based escape blocking, 86 lines of security tests.

## C++ Framework

- C++ SSE streaming parser (SHA 7ed2db3, #73): SSE parser (92 lines), Lemonade client integration (139 lines), tests (287 lines), CMakeLists.txt and README updates.

- C++ performance benchmarks (SHA 9c4101d): benchmark suite (335 lines), utilities (282 lines), mock LLM server (154 lines), CI workflow for binary size tracking (153 lines).

- C++ runtime configuration (SHA 878a976): agent.cpp (228 lines) with tool registry configuration, type system enhancements (84 lines), configuration tests.

## MCP Infrastructure

- MCP unit tests isolated from real `~/.gaia/mcp_servers.json` configuration (SHA e0e5695, #63): eliminated flaky test failures from shared server state.

## Test Infrastructure

- 151 integration tests across 9 files (2003 lines) achieving 88% code coverage (SHA 47c0c0c, #230): engine initialization, decision engine, execution phases, loop management, state machine transitions.

- 35 supervisor decision tests (881 lines) for quality scoring, escalation policies, defect routing, decision boundaries (SHA c3ccc4f, #120).

- 7 parallel execution edge-case test scenarios (444 lines): semaphore bounds, conflict overlap, rollback verdicts, worktree lifecycle, resource locking, timeout handling, supervisor failure recovery (SHA b3d707e, #211).

- Quality Gate 7 validation tests (1184 lines), quality gate report (356 lines), frontmatter parser tests (493 lines) (SHA f57e5ba, #253).

- SSE endpoint lock release tests (216 lines) and JSON serialization tests (178 lines) (SHA 3b6ebe6, #245).

- E2E pipeline timeout fix in test_full_pipeline.py after Session-2 timing changes (SHA 0ae23c9, #252).

## Quality Fixes

- Canvas wiring quality: quality scoring calculation fix, canvas wiring validation bug resolution, quality gate evaluation correction (SHA 574d142, #219).

- Canvas loop path fix: artifact propagation through loop iterations, state safety during looped execution, loop path resolution (SHA 961c7d5, #224).

- Canvas UI wiring fix: supervisor and loop node wiring, decision gate rendering, workspace visibility, canvas store state management, type definitions (SHA 1ffd7a6, #236).

- Canvas config quality bridge: canvas configuration wired to pipeline engine, quality scoring bridged between engine and recursive templates, resilience features enabled, recursive_template.py added (SHA 957a7cb, #237).

- Event loop consolidation in ThreadPoolExecutor threads (SHA 0ed82d4, #223): prevented resource contention under concurrent load.

- Final quality review fixes: event loop consolidation in orchestrator, loop manager thread handling, ThreadPoolExecutor resource contention (SHA 9bc85ec, #225).

- Pipeline isolation removed (SHA 03d15bd, #218): eliminated unnecessary PipelineIsolation overhead, fixed agent ID collision bugs.

- execute_tool dispatch fix across all pipeline stages (SHA 242e380, #250): enabled actual tool execution in pipelines.

- Pipeline CLI wiring: resolved hard-stop runtime bugs, wired gaia pipeline CLI commands, 122 lines of integration tests (SHA 71d5d48, #251).

- Session 3 quality review fixes: api.ts, pipelineStore.ts, types, routing_engine.py, pipeline router, schemas, comprehensive test suite (SHA 9b19f90, #248).

## Agent UI Fixes

- Agent UI Round 5: post-tool thinking display hidden, FileListView rendering fixed, text spacing corrected (SHA cc90935, #192).

- LRU eviction silent failure fixed: prevented unbounded memory growth in Agent UI (SHA 8a6452f).

- RAG indexing guards added for reliable document indexing, gaia init pip extras added (SHA af652d9, #193).

- Agent UI guardrails Round 6: guardrail fixes, rendering fixes, LRU eviction fixes, Windows path compatibility (SHA 95b304f, #194).

- Agent UI terminal: animation fixes, pixelated cursor fix, documentation update (SHA 25c6d25, #196).

- Reverted changes restored from PR #566 merge: TOCTOU security fix, tool guardrails, Agent UI improvements (SHA b7a97e6, #197).

- Lemonade v10 device key compatibility fix in lemonade_client.py (SHA 4015bb2).

- Tool guardrails: confirmation popup for tool execution in Agent UI (SHA 3df90ff, #191).

- Agent UI device guard: device detection with error message on unsupported devices (SHA 5dd71a2, #195).

- GAIA Chat UI: privacy-first desktop chat with document Q&A capabilities (SHA b2ace80).

## Metrics and Evaluation

- Metrics dashboard: collector (889 lines), hooks (596 lines), service (524 lines), template service (501 lines), 133 files changed with 20,948 insertions (SHA 5d167c4, #183).

- Pipeline eval metrics integration: eval_metrics.py (355 lines), eval metrics UI router (407 lines), comprehensive integration and unit tests (SHA 31de02f).

- Agent UI eval benchmark: gaia eval agent command, eval framework, eval runner, scorecard, documentation (SHA c72e6d9, #184).

- Missing metrics modules, agent definitions, and test modules added to complete pipeline orchestration infrastructure (SHA c290ed7, #78).

## Component Framework

- Component framework loader: loader utility (474 lines), frontmatter parser (410 lines), 24 templates across 6 categories, 860 lines of tests (SHA 57ee63d, #93).

- Component framework templates: 13 template types, 4 personas, 5 workflow patterns, tool calling guide (336 lines), Phase 3 Sprint 2 technical spec (2278 lines) (SHA e952716, #212).

## Orchestration Infrastructure

- ProjectSupervisor base class (548 lines) with lifecycle management, health checks, escalation policies, state persistence, 862 lines of tests (56 test cases) (SHA dd1d314, #203).

- GitSupervisor (519 lines) and supervisor registry (130 lines) with custom exception types, 559 lines of tests (SHA dc02956, #204).

- Automation hooks: monolithic hooks.py refactored into git_branch.py, git_commit.py, git_pr.py, git_rollback.py, objective_update.py, task_spawn.py, 787 lines of tests (SHA 6f95323, #205).

- DomainAnalyzer stage (365 lines) with component integration analysis, Phase 5 documentation suite (SHA 8d6ffdd, #209).

- WorkflowModeler stage (387 lines) for requirements analysis and workflow pattern selection (SHA a32187c, #113).

- LoomBuilder stage (426 lines) for execution topology construction (SHA 8dd22c1, #114).

- GapDetector (419 lines) for missing stage identification, orchestrator (518 lines) auto-spawn logic, user guide (353 lines) (SHA fa3ef98, #117).

- PipelineExecutor stage (488 lines) for agent lifecycle coordination and execution results (SHA 0c5f294, #118).

- Artifact provenance tracking in PipelineSnapshot for full traceability (SHA d3951f8, #217).

- Agent ecosystem display in Pipeline Runner UI (SHA f22f48a, #242): available agents and capabilities panel.

## CI/CD and Release

- npm OIDC trusted publishing: npm upgraded to 11.5.1+ in publish-npm-ui.yml, registry-url removed, token-based auth switched to OIDC.

- Merge-queue-notify phantom failure fix in .github/workflows/merge-queue-notify.yml (SHA 776dc34, #174).

- Agent UI build system (125 lines in build.py) added to gaia init command, 314 lines of build tests (SHA bb010a0, #172).

- WebUI version bumped to 0.17.1 (SHA b19d812).

- Releases v0.17.0 and v0.17.1 with version bumps and release notes.

## Repository Maintenance

- .gitignore updated to exclude runtime artifacts and untrack chroma_data/chroma.sqlite3 (SHA ad4f7c6, #51).

- 62 historical documents archived to docs/archive/, docs.json navigation updated, ether-repl-spec.md (504 lines) added (SHA 76675ea).

- .claude/ directory removed from git tracking, 24 agent definition files cleaned up (SHA d14e3fe).

- Branch change matrix (957 lines) created for feature/pipeline-orchestration-v1 (SHA 79b1861).
