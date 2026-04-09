---
title: Nexus GAIA Native Integration Specification
description: Complete specification for implementing all Nexus system capabilities natively in GAIA, including 29 new Python modules across 8 packages for intent orchestration, PII sanitization, knowledge harvesting, and GitHub integration.
status: Published
---

# Nexus → GAIA Native Integration Specification

**Version:** 1.0
**Date:** 2026-04-01
**Status:** APPROVED (post quality-review pipeline, 3 iterations)
**Scope:** Native Python implementation of all Nexus capabilities inside `src/gaia/`

---

## 1. Purpose

This document specifies the complete set of Python modules, classes, and architectural changes required to implement all Nexus system capabilities natively inside the GAIA Python framework (`src/gaia/`). The goal is that any developer working in the GAIA project gets the full Nexus-equivalent capability set automatically — without depending on an external plugin folder or global user-level hook scripts.

**What this is NOT:**
- This is NOT about `~/.claude/hooks.json` wiring
- This is NOT about copying hook scripts between folders
- This is NOT about `.claude/` directory structure

**What this IS:**
- 28 new Python modules across 8 new package directories in `src/gaia/`
- Extensions to 5 existing GAIA Python modules
- Proper, tested, importable GAIA architecture that mirrors Nexus capabilities

---

## 2. Current State: What GAIA Already Has

| Module | Location | What It Does | Nexus Equivalent |
|--------|----------|--------------|-----------------|
| `HookRegistry` + `HookExecutor` | `src/gaia/hooks/registry.py` | Event-based, priority-sorted, thread-safe hook system. 17 HookEvents. | Nexus hooks.json dispatch |
| `BaseHook` + `HookContext` + `HookResult` | `src/gaia/hooks/base.py` | Base class and data types for all hooks. `HookEvent` enum: PIPELINE_START, PIPELINE_COMPLETE, PIPELINE_FAILED, PIPELINE_CANCELLED, PHASE_ENTER, PHASE_EXIT, LOOP_START, LOOP_END, AGENT_SELECT, AGENT_EXECUTE, AGENT_COMPLETE, QUALITY_EVAL, QUALITY_RESULT, DECISION_MAKE, DEFECT_EXTRACT, CONTEXT_INJECT, OUTPUT_PROCESS | Nexus hook event types |
| Production Hooks (3 files) | `src/gaia/hooks/production/` | ContextInjectionHook, OutputProcessingHook, QualityGateHook, DefectExtractionHook, ChronicleHarvestHook, PipelineNotificationHook, PreActionValidationHook, PostActionValidationHook | Nexus quality + context hooks |
| `PipelineEngine` | `src/gaia/pipeline/engine.py` | Full async pipeline orchestrator. Integrates AgentRegistry, QualityScorer, LoopManager, DecisionEngine, RoutingEngine, HookRegistry. PipelinePhase: PLANNING, DEVELOPMENT, QA, TESTING, DEPLOYMENT | Nexus recursive pipeline executor |
| `AuditLogger` | `src/gaia/pipeline/audit_logger.py` | Tamper-proof hash-chain audit trail. AuditEventType: PIPELINE_START, PHASE_ENTER/EXIT, AGENT_SELECTED/EXECUTED, QUALITY_EVALUATED, DECISION_MADE, DEFECT_DISCOVERED/REMEDIATED, LOOP_BACK, TOOL_EXECUTED | Nexus chronicle store |
| `DecisionEngine` | `src/gaia/pipeline/decision_engine.py` | DecisionType: CONTINUE, LOOP_BACK, PAUSE, COMPLETE, FAIL. Quality-gate based decisions. | Nexus pipeline planner decisions |
| `LoopManager` | `src/gaia/pipeline/loop_manager.py` | Recursive loop management, LoopConfig, loop iteration tracking | Nexus recursive loop control |
| `RoutingEngine` | `src/gaia/pipeline/routing_engine.py` | Routes defects to agents by DefectType + DEFECT_SPECIALISTS mapping | Nexus category agent router |
| `RecursivePipelineTemplate` | `src/gaia/pipeline/recursive_template.py` | Template-driven pipeline with RECURSIVE_TEMPLATES dict, AgentCategory enum (PLANNING, DEVELOPMENT, REVIEW, MANAGEMENT, QUALITY, DECISION) | Nexus auto-pilot templates |
| `DefectRemediationTracker` | `src/gaia/pipeline/defect_remediation_tracker.py` | Full defect lifecycle: OPEN→IN_PROGRESS→RESOLVED→VERIFIED | Nexus session progress tracking |
| `PipelineStateMachine` | `src/gaia/pipeline/state.py` | PipelineContext, PipelineSnapshot, PipelineState — in-process only | Nexus session state (partial) |
| `PipelineMetricsCollector` | `src/gaia/pipeline/metrics_collector.py` | Token efficiency, phase duration, defect rates | Nexus performance metrics |
| `QualityScorer` | `src/gaia/quality/scorer.py` | 27 validation categories across 6 dimensions. Full async evaluation. | Nexus quality guardian (exceeds it) |
| `QualityReport` + models | `src/gaia/quality/models.py` | CategoryScore, DimensionScore, CertificationStatus, QualityWeightConfig | Nexus quality models |
| Quality validators | `src/gaia/quality/validators/` | SecurityValidator (5 auth patterns), CodeValidators, DocsValidators, RequirementsValidators, TestValidators | Nexus quality checks (partial) |
| `AgentRegistry` | `src/gaia/agents/registry.py` | YAML-based, hot-reload, capability-based routing, state-based activation, thread-safe. AGENT_CATEGORIES dict, select_agent(), register(), hot_reload() | Nexus agent registry (partial) |
| `AgentDefinition` + base classes | `src/gaia/agents/base/` | AgentCapabilities, AgentConstraints, AgentTriggers | Nexus agent definitions |
| RAG SDK | `src/gaia/rag/sdk.py` | Document Q&A via FAISS + sentence_transformers. ChromaDB NOT used here. | Nexus vector store (different domain) |
| `FileWatcher` | `src/gaia/utils/file_watcher.py` | Hot-reload file monitoring | Used by new agent extensions |
| `device.py` | `src/gaia/device.py` | AMD GPU/NPU hardware detection | Nexus env sync (partial, hardware only) |

---

## 3. Capability Gap Matrix

### Legend
- **Status**: DONE = already in GAIA | TODO = must be built | EXTEND = existing module needs extension
- **Priority**: P1 = Sprint 1 (foundation) | P2 = Sprint 2 (capabilities) | P3 = Sprint 3 (integrations/hardening)

---

### 3.1 Input Orchestration

| Field | Value |
|-------|-------|
| **Capability** | Intent Orchestration |
| **Nexus Source** | `nexus_input_orchestrator.py`, `enhanced_agent_command_router.py` |
| **What It Does** | Classifies raw user prompt into 1 of 6 intent types via regex + optional semantic router. Gathers context (CWD, git diff, open files, env metadata). Injects "thinking trigger" prompts into Claude's context. Routes to specialist agent by intent. Retrieves similar past strategies from vector store. Lightweight `analyze_context` mode for pre-compaction. |
| **Intent Types** | ECOSYSTEM_CREATION, AGENT_CREATION, COMPONENT_CREATION, CODE_REFACTORING, QUALITY_AUDIT, SYSTEM_COMMAND |
| **GAIA Currently Has** | `RoutingEngine` routes defects to agents by DefectType inside a running pipeline. `AgentRegistry.select_agent()` does capability matching. `ContextInjectionHook` injects prior pipeline results at AGENT_EXECUTE. |
| **Gap** | No pre-task prompt classification. No raw-prompt context gathering. No git-state snapshotting before pipeline starts. No thinking-trigger injection. No `analyze_context` lightweight mode. GAIA's routing is internal to a running pipeline; Nexus routes at the user-prompt boundary. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/orchestration/intent_orchestrator.py`, `src/gaia/orchestration/context_gatherer.py`, `src/gaia/orchestration/thinking_triggers.py` (P2) |
| **Key Classes** | `IntentOrchestrator`, `IntentType` (enum, 6 values), `IntentClassification`, `ContextGatherer`, `ContextSnapshot`, `ThinkingTriggerEngine`, `TriggerType` |
| **Critical Design Note** | `IntentOrchestrator` MUST implement two explicit entry points: `from_hook_context(hook_context: HookContext)` for pipeline-internal calls and `from_claude_code_session(additional_context: dict)` for Claude Code hook calls. Without this, the class accumulates caller-detection anti-patterns. |
| **Integrates With** | `RoutingEngine` (existing), `AgentRegistry.select_agent()` (existing), `StrategyStore` (new, item 3.3), `ContextGatherer` (new) |

---

### 3.2 Environment Sync

| Field | Value |
|-------|-------|
| **Capability** | Environment Detection and Feature Toggles |
| **Nexus Source** | `nexus_environment_sync.py` |
| **What It Does** | Detects OS, shell, Python version, git branch/status. Manages feature toggles (enable/disable GAIA subsystems at runtime via YAML config). Injects environment snapshot as `additionalContext` on every Claude Code session start. Detects "configuration drift" and triggers thinking. |
| **GAIA Currently Has** | `device.py` — AMD GPU/NPU hardware detection only. No OS/shell/git detection module. No feature toggle system. No env snapshot injection into Claude context. |
| **Gap** | Complete gap on all non-hardware env detection. Feature toggles (enable/disable knowledge harvesting, PII sanitizer, GitHub integration, etc.) do not exist anywhere in `src/gaia/`. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/environment/detector.py`, `src/gaia/environment/feature_toggles.py`, `src/gaia/environment/sync_service.py` |
| **Key Classes** | `EnvironmentDetector`, `EnvironmentSnapshot` (dataclass), `FeatureToggleManager`, `EnvironmentSyncService` |
| **Integrates With** | `FileWatcher` from `gaia/utils/file_watcher.py` (existing — for hot-reload of toggle YAML). `ContextGatherer` (new, item 3.1 — reuses OS/shell detection). |
| **Config File** | `~/.gaia/feature_toggles.yml` — controls which GAIA subsystems are active |

---

### 3.3 Knowledge Harvesting

| Field | Value |
|-------|-------|
| **Capability** | Developer-Session Knowledge Capture and Retrieval |
| **Nexus Source** | `nexus_knowledge_harvester.py`, `chroma_vector_store_manager_v2.py` |
| **What It Does** | Captures structured insights from completed tasks (what worked, what failed). Pattern recognition: success/failure patterns across sessions. Writes to a dedicated ChromaDB collection for developer knowledge (NOT document Q&A). Retrieves similar past strategies when a new task matches a known intent type. |
| **GAIA Currently Has** | `ChronicleHarvestHook` in `quality_hooks.py` — records pipeline lifecycle events into chronicle. `gaia/rag/sdk.py` — ChromaDB NOT used (FAISS + sentence_transformers for document Q&A). `gaia/metrics/` — token efficiency and defect rates, not developer heuristics. |
| **Gap** | No module captures "this regex approach worked for this intent type" developer heuristics across conversations. `ChronicleHarvestHook` records events but does not extract insight summaries for future retrieval. RAG SDK is document-scoped (different domain). |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/knowledge/models.py`, `src/gaia/knowledge/strategy_store.py`, `src/gaia/knowledge/harvester.py` |
| **Key Classes** | `InsightType` (enum: STRATEGY_SUCCESS, STRATEGY_FAILURE, AGENT_PATTERN, DEFECT_PATTERN), `KnowledgeInsight` (dataclass), `StrategyRetrievalResult`, `StrategyStore`, `KnowledgeHarvester` |
| **ChromaDB Collection** | `gaia_strategy_knowledge` — MUST be a distinct collection name; never share with RAG SDK |
| **New Pip Dependency** | `chromadb>=0.4.0` — check if already in `pyproject.toml` (note: `chroma_data/` exists in repo root, ChromaDB may already be installed) |
| **Critical Architecture Note** | Do NOT extend `ChronicleHarvestHook` to call `KnowledgeHarvester`. This violates SRP. Instead, create a NEW `KnowledgeHarvestHook` in `quality_hooks.py` registered at LOW priority on `PIPELINE_COMPLETE`, reading finalized chronicle data. |
| **Integrates With** | New `KnowledgeHarvestHook` (registered at PIPELINE_COMPLETE, LOW priority). `IntentOrchestrator.from_hook_context()` (retrieves strategies). `StrategyStore` (ChromaDB write/read). |

---

### 3.4 PII and Content Sanitization

| Field | Value |
|-------|-------|
| **Capability** | PII Detection, Redaction, and Compliance Audit Trail |
| **Nexus Source** | `content_sanitizer_hook.py`, `content_sanitizer.py`, `audit_log_manager.py` |
| **What It Does** | Detects and redacts PII across 38 patterns in 6 categories before conversation compaction. Categories: personal (SSN, passport, driver's license), contact (email, phone, address), financial (credit card, IBAN, bank account), auth (API keys, AWS keys, GitHub tokens, passwords), health (MRN, HIPAA IDs), corporate (CONFIDENTIAL, PROPRIETARY markings). Auto-redact mode or warning-only. Maintains tamper-proof compliance audit trail. Configurable sensitivity: strict (≥0.5), moderate (≥0.75), relaxed (≥0.90). |
| **GAIA Currently Has** | `SecurityValidator` (BP-01) in `gaia/quality/validators/security_validators.py` — 5 auth credential patterns, code-oriented, detection only, no redaction, no other PII categories. `AuditLogger` in `gaia/pipeline/audit_logger.py` — hash-chain pipeline event log, not PII-specific. |
| **Gap** | Zero coverage of 5 of 6 PII categories (personal, contact, financial, health, corporate). `SecurityValidator` detects but never redacts. `AuditLogger` records pipeline events, not PII compliance events. No `PRE_COMPACT` hook exists in GAIA. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/security/pii_sanitizer.py`, `src/gaia/security/pii_audit_logger.py`, `src/gaia/hooks/production/compaction_hooks.py` |
| **Key Classes** | `PIISanitizer`, `SensitivityLevel` (enum: STRICT, MODERATE, RELAXED), `RedactionMode` (enum: AUTO_REDACT, WARNING_ONLY), `SanitizerConfig`, `PIIDetection`, `SanitizationResult`, `PIIAuditLogger` |
| **HookEvent Addition** | Add `PRE_COMPACT` to `HookEvent` enum in `gaia/hooks/base.py`. `CompactionPreHook(BaseHook)` listens on this event. |
| **Integrates With** | `SecurityValidator` — import its 5 existing patterns, extend library to 38 total. `AuditLogger` — `PIIAuditLogger` wraps (composition, not inheritance) to add PII record schema to the hash chain. `BaseHook`, `HookContext`, `HookResult` (existing). |

---

### 3.5 Quality Guardian (File-Write)

| Field | Value |
|-------|-------|
| **Capability** | Per-File-Write Fast Heuristic Quality Check |
| **Nexus Source** | `nexus_quality_guardian.py` |
| **What It Does** | Runs on every file write event (Claude Code PostToolUse). Fast heuristic scoring: readability (line length, function length), compliance (forbids TODO:, FIXME:, ad-hoc comment markers), structural integrity (complexity estimate). Returns violations as warnings — never blocks. Exhaustive mode available on task completion (calls full QualityScorer). |
| **GAIA Currently Has** | `QualityScorer` — 27 categories, async, full evaluation. `QualityGateHook` — fires at PHASE_EXIT inside a running pipeline. These are far more rigorous but fire at pipeline boundaries, not at individual file writes. |
| **Gap** | The trigger point is the gap. GAIA's quality hooks fire inside a running `PipelineEngine`. There is no hook that fires at the Claude Code `PostToolUse` layer (file write events from Claude Code IDE). `QualityGateHook` cannot receive Claude Code subprocess calls. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/quality/heuristic_scorer.py`, `src/gaia/hooks/production/file_write_hooks.py` |
| **Key Classes** | `HeuristicFileScorer`, `HeuristicScore` (dataclass: readability, compliance, structural, violations list) |
| **CRITICAL Architecture Note** | `file_write_hooks.py` MUST be implemented as a `__main__`-capable standalone script that reads Claude Code JSON from stdin and calls `HeuristicFileScorer` directly. It is NOT a `BaseHook` subclass. Claude Code calls it as a subprocess; it cannot be dispatched through `HookRegistry.emit()`. `POST_TOOL_USE` should NOT be added to `HookEvent` enum unless a bridge service is also built (P3). |
| **Integrates With** | `SecurityValidator` (import existing patterns for compliance checks). `QualityScorer` (existing — called in exhaustive mode only, not on every write). |

---

### 3.6 Auto-Pilot / Self-Prompting Pipeline

| Field | Value |
|-------|-------|
| **Capability** | Trigger Detection, Self-Prompting, Cross-Conversation Pipeline Continuity |
| **Nexus Source** | `auto_pilot_hook.py`, `auto_pilot_detector.py`, `recursive_pipeline_executor.py` |
| **What It Does** | Detects auto-pilot trigger suffixes in user prompts (`*professional`, `*ap`, `*quality`, `*standard`). Maps suffix to a `RecursivePipelineTemplate`. Generates self-continuation prompts so Claude Code does not stop mid-pipeline. Persists pipeline state to disk so a resumed conversation can continue from the correct phase. |
| **GAIA Currently Has** | `PipelineEngine` — full async orchestrator (called programmatically). `RecursivePipelineTemplate` with `RECURSIVE_TEMPLATES` dict. `LoopManager` for iteration control. These all run inside a process; they cannot inject prompts into Claude's context or survive process restarts. |
| **Gap** | No trigger detection from raw user prompts. No self-prompting mechanism (generating continuation instructions into `additionalContext`). No disk-persistent state for cross-conversation continuity. `PipelineEngine.run()` cannot self-continue; it is called once and returns. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/orchestration/auto_pilot_detector.py`, `src/gaia/orchestration/auto_pilot_state.py`, `src/gaia/orchestration/self_prompt_injector.py` |
| **Key Classes** | `AutoPilotDetector`, `AutoPilotTrigger` (dataclass: suffix, pipeline_template, quality_threshold), `AutoPilotStateStore`, `AutoPilotState`, `SelfPromptInjector` |
| **State Persistence** | `~/.gaia/auto_pilot_state.json` — stores pipeline_id, current_phase, session_id, template_name, iteration_count, last_active. File-based JSON recommended for portability. |
| **Integrates With** | `RecursivePipelineTemplate` and `RECURSIVE_TEMPLATES` dict (existing — maps trigger to template name). `PipelineEngine.run()` (existing — called with the template). `BuildSessionManager` (new, item 3.7). |

---

### 3.7 Session Management

| Field | Value |
|-------|-------|
| **Capability** | Multi-Conversation Build Plans with Resume and Stop Guard |
| **Nexus Source** | `session_manager.py`, `session_start_hook.py`, `session_command_router.py`, `session_tracker_hook.py`, `session_stop_guard.py` |
| **What It Does** | Persists multi-conversation build plans to disk (`~/.gaia/session-data/`). Tracks component creation progress across separate Claude Code invocations. Parses session commands: `session:continue`, `session:status`, `session:complete`, `session:list`. Prevents Claude Code from stopping mid-build via stop guard. |
| **GAIA Currently Has** | `PipelineStateMachine` — holds pipeline state in-process only; does not survive process restart. `gaia/ui/routers/sessions.py` — FastAPI endpoints for chat UI session CRUD (completely different concept: which chat messages belong together). |
| **Gap** | GAIA "sessions" = chat history groupings. Nexus "sessions" = multi-conversation build plans (which components of an ecosystem have been built across multiple separate Claude Code invocations). Entirely different constructs. `PipelineStateMachine` state dies with the process. `session:continue` resume mechanism does not exist. |
| **Status** | TODO |
| **Priority** | P1 |
| **Modules to Build** | `src/gaia/session/build_plan.py`, `src/gaia/session/build_session_manager.py`, `src/gaia/session/session_command_router.py`, `src/gaia/session/stop_guard.py` |
| **Key Classes** | `BuildPlan`, `ComponentStatus` (enum: PENDING, IN_PROGRESS, COMPLETE, FAILED), `ComponentType` (enum: 13 Nexus component types), `BuildSessionManager`, `SessionCommandRouter`, `SessionCommand`, `StopGuard` |
| **State Persistence** | `~/.gaia/session-data/{session_name}/plan.json` — JSON serialized `BuildPlan`. References `PipelineContext` IDs for completed components. |
| **HookEvent Addition** | Add `STOP` to `HookEvent` enum in `gaia/hooks/base.py`. `StopGuard` registers as a `BaseHook` subclass on this event. |
| **CRITICAL Architectural Rule** | `gaia/pipeline/` (especially `engine.py`) MUST NEVER import from `gaia/session/`. `BuildSessionManager` calls `PipelineEngine.run()` but not the reverse. Enforce with a lint rule or import boundary test in `tests/`. One bad import closes the circular dependency. |
| **Integrates With** | `PipelineContext` (existing — stores pipeline_id references). `AutoPilotStateStore` (new, item 3.6). `IntentOrchestrator` (new, item 3.1 — `SessionCommandRouter` is called as pre-classification step). |

---

### 3.8 Agent Mention System

| Field | Value |
|-------|-------|
| **Capability** | @Mention Detection, Markdown Agent Scanning, Global Pool Sync |
| **Nexus Source** | `agent_mention_hook.py`, `agent_mention_assistant.py`, `agent_discovery_cache.py`, `agent_command_auto_load_hook.py`, `sync_agent_to_global.py` |
| **What It Does** | Detects `@agent-name` tokens in raw user prompts. Resolves names against all known agents (both YAML and markdown definitions). Injects matched agent capabilities as `additionalContext`. Scans `~/.claude/agents/*.md` for markdown agent definitions. Keeps project-local agents synced to the global pool. |
| **GAIA Currently Has** | `AgentRegistry.select_agent()` — takes structured parameters (DefectType, AgentCategory); cannot parse raw prompt strings for `@name` tokens. `AgentRegistry` reads YAML files only; no markdown parsing. |
| **Gap** | No `@mention` syntax parsing anywhere in GAIA. No `~/.claude/agents/` directory scanning. No markdown frontmatter parsing pipeline. No auto-sync of project agents to global pool. |
| **Status** | TODO |
| **Priority** | P2 |
| **Modules to Build** | `src/gaia/agents/markdown_scanner.py`, `src/gaia/agents/hook_injector.py`, `src/gaia/agents/mention_detector.py`, `src/gaia/agents/global_pool_syncer.py` |
| **Key Classes** | `MarkdownAgentScanner`, `HookInjector`, `AgentMentionDetector`, `GlobalPoolSyncer` |
| **Integrates With** | `AgentRegistry.register()` (existing — `MarkdownAgentScanner` feeds it). `AgentDefinition` (existing base class). `FileWatcher` (existing — hot-reload for markdown file changes). |

---

### 3.9 Category Agent Router

| Field | Value |
|-------|-------|
| **Capability** | Phase-Based Task-to-Agent Routing via Keyword Matching |
| **Nexus Source** | `category_agent_router.py`, `plugin-config.yml` phase definitions |
| **What It Does** | Routes tasks to agents by capability phase (Research, Planning, Development, QA, Testing, Deployment). Matches agent descriptions to phase keywords from config. |
| **GAIA Currently Has** | `RoutingEngine` + `AgentRegistry.AGENT_CATEGORIES` + `AgentCategory` enum in `RecursivePipelineTemplate` — collectively implement phase-based routing with MORE sophistication than Nexus's router. |
| **Gap** | Minor: `MarkdownAgentScanner` (item 3.8) needs to populate `AgentRegistry.AGENT_CATEGORIES` from markdown frontmatter `phases:` field so that markdown-defined agents are routable by phase. |
| **Status** | EXTEND |
| **Priority** | P3 |
| **Action** | Extend `src/gaia/agents/markdown_scanner.py` (item 3.8) to parse `phases:` frontmatter field and call `AgentRegistry.AGENT_CATEGORIES` population logic. No new module needed. |
| **Integrates With** | `MarkdownAgentScanner` (new, item 3.8), `AgentRegistry` (existing). |

---

### 3.10 GitHub Event Integration

| Field | Value |
|-------|-------|
| **Capability** | GitHub Event Polling, Pub/Sub Event Bus, Webhook Server, Pipeline Triggering |
| **Nexus Source** | `github_context_hook.py`, `github_event_poller.py`, `github_event_bus.py`, `github_event_handler.py`, `github_webhook_server.py`, `github_pipeline_trigger.py`, `github_integration.py` |
| **What It Does** | Polls GitHub Events API every 60s (respects `X-Poll-Interval` response header). Pub/sub event bus with wildcard subscriptions (`issues.*`, `pull_request.*`). Receives webhook push events (HMAC-SHA256 validated). Triggers GAIA pipeline sessions on configured GitHub events. Posts quality gate results back as PR reviews. |
| **GAIA Currently Has** | Nothing. Zero GitHub integration in `src/gaia/`. JIRA agent (`agents/jira/`) is the only external service integration. |
| **Gap** | Complete gap. Entire subsystem must be built. |
| **Status** | TODO |
| **Priority** | P2 |
| **Modules to Build** | `src/gaia/integrations/github/event_poller.py`, `src/gaia/integrations/github/event_bus.py`, `src/gaia/integrations/github/webhook_server.py`, `src/gaia/integrations/github/pipeline_trigger.py`, `src/gaia/integrations/github/pr_quality_adapter.py` |
| **Key Classes** | `GitHubEventPoller`, `PollerStatus` (enum), `GitHubEvent`, `GitHubEventBus`, `SubscriptionInfo`, `GitHubWebhookServer`, `GitHubPipelineTrigger`, `RouteConfig`, `PRQualityGateAdapter`, `PRReviewPayload` |
| **New Pip Dependencies** | `httpx` or `PyGithub` (GitHub REST API calls in `event_poller.py`). FastAPI already in GAIA (reuse for `webhook_server.py`). |
| **Integrates With** | `HookRegistry`, `HookExecutor` (existing — `GitHubEventBus` wraps them). `AuditLogger` (existing — logs polling errors). `gaia/security.py` (existing — HMAC validation). `BuildSessionManager` (new, item 3.7 — trigger creates sessions). `PipelineEngine.run()` (existing). `QualityScorer` + `QualityReport` (existing — `PRQualityGateAdapter` adapts output). |

---

### 3.11 PR Quality Gates

| Field | Value |
|-------|-------|
| **Capability** | Pull Request Quality Gate Posting |
| **Nexus Source** | `pr_quality_gates.py` |
| **What It Does** | Maps GAIA's `QualityReport` output to GitHub PR review body format. Posts gate results (code quality, security, completeness) as GitHub PR reviews via API. |
| **GAIA Currently Has** | `QualityScorer` with 27 categories covering code quality, docs, security, test coverage — content-level coverage substantially present. `QualityGateHook` enforces thresholds. No GitHub PR posting. |
| **Gap** | GitHub PR integration adapter only. No new scoring logic needed — QualityScorer already covers Gate 2, Gate 3, Gate 5 content. |
| **Status** | TODO |
| **Priority** | P3 |
| **Modules to Build** | `src/gaia/integrations/github/pr_quality_adapter.py` (part of item 3.10) |
| **Key Classes** | `PRQualityGateAdapter`, `PRReviewPayload` |
| **Integrates With** | `QualityReport`, `DimensionScore`, `CategoryScore` (existing `gaia/quality/models.py`). `QualityScorer` (existing). GitHub API client from item 3.10. |

---

### 3.12 Chronicle Store with PII Filtering

| Field | Value |
|-------|-------|
| **Capability** | Append-Only Event Log with PII Redaction Before Storage |
| **Nexus Source** | `chronicle_store.py`, `chronicle_hooks.py` |
| **What It Does** | Append-only event log with PII redaction applied before storage. ChronicleEntry: id, timestamp, agentId, eventType, payload. Event types: THOUGHT, TOOL_CALL, TOOL_RESULT, CONSENSUS, ERROR. Cross-platform file locking (fcntl/Windows fallback). |
| **GAIA Currently Has** | `AuditLogger` — hash-chain audit trail with tamper-proof integrity. AuditEventType covers pipeline lifecycle (PIPELINE_START, PHASE_ENTER/EXIT, AGENT_SELECTED/EXECUTED, QUALITY_EVALUATED, etc.). No PII redaction. Missing event types: THOUGHT, CONSENSUS. |
| **Gap** | (a) `AuditLogger` stores payloads verbatim — no PII redaction pass before hash-and-append. (b) Missing `AuditEventType` values: THOUGHT (agent cognitive steps), CONSENSUS (multi-agent agreement). (c) No cross-platform file locking (though Python's fcntl behavior covers most cases). |
| **Status** | EXTEND |
| **Priority** | P2 |
| **Action** | Extend `src/gaia/pipeline/audit_logger.py`: (a) Add THOUGHT, CONSENSUS to `AuditEventType` enum. (b) Add optional `PIISanitizationFilter` pre-write callback — if `PIISanitizer` (item 3.4) is registered, sanitize payload before hashing and appending. (c) Verify file-locking behavior on Windows. |
| **Integrates With** | `PIISanitizer` (new, item 3.4 — called as pre-write filter). `AuditLogger` (existing — extension, not replacement). |

---

## 4. Existing GAIA Modules Extended (Not Replaced)

| Module | Extension | Required By |
|--------|-----------|-------------|
| `src/gaia/hooks/base.py` `HookEvent` enum | Add `PRE_COMPACT` and `STOP` values. Do NOT add `POST_TOOL_USE` unless a bridge service is built (P3). Note: `OUTPUT_PROCESS` already exists (17th value). | Items 3.4, 3.7 |
| `src/gaia/pipeline/audit_logger.py` `AuditLogger` | Add `AuditEventType.THOUGHT` and `AuditEventType.CONSENSUS`. Add optional `PIISanitizationFilter` pre-write callback hook. | Items 3.3, 3.12 |
| `src/gaia/hooks/production/quality_hooks.py` | Add new `KnowledgeHarvestHook` class (LOW priority, PIPELINE_COMPLETE event) that reads finalized chronicle and calls `KnowledgeHarvester.harvest()`. Do NOT modify `ChronicleHarvestHook`. | Item 3.3 |
| `src/gaia/quality/validators/security_validators.py` `SecurityValidator` | Export existing 5-pattern library for reuse by `PIISanitizer` (which extends to 38 patterns total) and `HeuristicFileScorer`. | Items 3.4, 3.5 |
| `src/gaia/agents/registry.py` `AgentRegistry` | Verify `register()` accepts `AgentDefinition` objects from `MarkdownAgentScanner` (likely compatible via base class already). If not, add overloaded `register_from_markdown()`. | Item 3.8 |

---

## 5. Complete Build List (Ordered by Dependency)

### Sprint 1 — Foundation (P1)

All Sprint 1 items must be complete before Sprint 2 begins. These have no inter-sprint dependencies.

```
# 1A — Environment Layer (no dependencies)
src/gaia/environment/detector.py
src/gaia/environment/feature_toggles.py
src/gaia/environment/sync_service.py

# 1B — Security/PII Layer (no dependencies)
src/gaia/security/pii_sanitizer.py
src/gaia/security/pii_audit_logger.py
src/gaia/hooks/production/compaction_hooks.py

# 1C — Session Layer (no dependencies on new modules; uses existing PipelineContext)
src/gaia/session/build_plan.py
src/gaia/session/build_session_manager.py    ← MUST NOT import gaia.pipeline
src/gaia/session/session_command_router.py
src/gaia/session/stop_guard.py

# 1D — Orchestration Layer (depends on 1A complete)
src/gaia/orchestration/intent_orchestrator.py  ← TWO entry points required
src/gaia/orchestration/context_gatherer.py
src/gaia/orchestration/auto_pilot_detector.py
src/gaia/orchestration/auto_pilot_state.py
src/gaia/orchestration/self_prompt_injector.py

# 1E — Quality Guardian (no dependencies)
src/gaia/quality/heuristic_scorer.py
src/gaia/hooks/production/file_write_hooks.py   ← __main__ script, NOT BaseHook

# 1F — HookEvent Extensions
# Edit: src/gaia/hooks/base.py
# Add to HookEvent enum: PRE_COMPACT, STOP
# Do NOT add POST_TOOL_USE at this stage

# 1G — Architectural Guard (enforce immediately)
# Add lint rule or import boundary test:
#   gaia/pipeline/*.py must never import from gaia/session/
```

### Sprint 2 — Capabilities (P1/P2)

```
# 2A — Knowledge Layer (depends on KnowledgeHarvestHook design from Sprint 1)
src/gaia/knowledge/models.py
src/gaia/knowledge/strategy_store.py    ← ChromaDB, collection: "gaia_strategy_knowledge"
src/gaia/knowledge/harvester.py

# 2B — Chronicle Extension (depends on PIISanitizer from 1B)
# Edit: src/gaia/pipeline/audit_logger.py
# Add AuditEventType.THOUGHT, AuditEventType.CONSENSUS
# Add PIISanitizationFilter pre-write callback

# 2C — New KnowledgeHarvestHook (depends on KnowledgeHarvester from 2A)
# Edit: src/gaia/hooks/production/quality_hooks.py
# Add KnowledgeHarvestHook(BaseHook), LOW priority, PIPELINE_COMPLETE
# Do NOT modify ChronicleHarvestHook

# 2D — Agent Extensions (depends on MarkdownAgentScanner feeding AgentRegistry)
src/gaia/agents/markdown_scanner.py
src/gaia/agents/hook_injector.py
src/gaia/agents/mention_detector.py
src/gaia/agents/global_pool_syncer.py

# 2E — Thinking Triggers (depends on IntentOrchestrator from 1D being stable)
src/gaia/orchestration/thinking_triggers.py

# 2F — GitHub Integration
src/gaia/integrations/__init__.py
src/gaia/integrations/github/__init__.py
src/gaia/integrations/github/event_poller.py
src/gaia/integrations/github/event_bus.py
src/gaia/integrations/github/webhook_server.py
src/gaia/integrations/github/pipeline_trigger.py
src/gaia/integrations/github/pr_quality_adapter.py
```

### Sprint 3 — Hardening (P3)

```
# 3A — Category Router Extension (minor, depends on MarkdownAgentScanner from 2D)
# Edit: src/gaia/agents/markdown_scanner.py
# Add phases: frontmatter field parsing → populate AgentRegistry.AGENT_CATEGORIES

# 3B — POST_TOOL_USE Bridge (only if needed)
# If file_write_hooks.py __main__ script proves insufficient:
# Add HookEvent.POST_TOOL_USE to base.py
# Build bridge service for Claude Code → HookRegistry dispatch

# 3C — Integration Tests
tests/integration/test_session_pipeline_boundary.py
# Verifies: gaia/pipeline/*.py never imports gaia/session/
tests/integration/test_knowledge_harvester.py
tests/integration/test_pii_sanitizer.py

# 3D — Documentation corrections
# Correct docs/spec/ references:
# - Add OUTPUT_PROCESS to existing HookEvent list
# - Correct StrategyStore isolation rationale (gaia/rag/ uses FAISS, not ChromaDB)
# - Correct module count in Orchestration Layer (6 modules, not 5)
```

---

## 6. New Python Dependencies

| Package | Version | Sprint | Purpose | Check |
|---------|---------|--------|---------|-------|
| `chromadb` | `>=0.4.0` | Sprint 2 | `StrategyStore` vector store for developer knowledge | Check `pyproject.toml` — `chroma_data/` exists in repo root, may already be installed |
| `httpx` or `PyGithub` | latest stable | Sprint 2 | GitHub REST API calls in `GitHubEventPoller` | Not currently in GAIA dependencies |
| `fastapi` | already present | Sprint 2 | `GitHubWebhookServer` — reuse existing GAIA FastAPI dep | Confirm in `pyproject.toml` |

**Add to `pyproject.toml` under `[project.optional-dependencies]`:**
```toml
[project.optional-dependencies]
nexus = [
    "chromadb>=0.4.0",
    "httpx>=0.27.0",
]
```

**Install with:**
```bash
pip install -e ".[nexus]"
```

---

## 7. Architecture Diagram: Integrated GAIA Target State

```
╔══════════════════════════════════════════════════════════════════════════════╗
║               INTEGRATED GAIA ARCHITECTURE — TARGET STATE                   ║
║   [EXISTS] = already in src/gaia/    [NEW] = must be built this spec        ║
╚══════════════════════════════════════════════════════════════════════════════╝

  Claude Code Runtime Events (hooks.json → subprocess calls)
  ┌─────────────────────────────────────────────────────────────────┐
  │ SessionStart │ UserPromptSubmit │ PostToolUse │ PreCompact │ Stop│
  └────┬─────────┴────────┬─────────┴──────┬──────┴─────┬──────┴─┬──┘
       │                  │                │            │         │
  ╔════▼═══╗         ╔════▼══════════╗ ╔══▼════╗  ╔════▼══╗ ╔════▼══╗
  ║[NEW]   ║         ║[NEW]          ║ ║[NEW]  ║  ║[NEW]  ║ ║[NEW]  ║
  ║Environ-║         ║Orchestration  ║ ║File   ║  ║Compact║ ║Stop   ║
  ║mentSync║         ║Entrypoint     ║ ║Write  ║  ║PreHook║ ║Guard  ║
  ╚════╤═══╝         ║(from_claude   ║ ║(__main║  ║(Compac║ ╚════╤══╝
       │             ║ _code_session)║ ║ script║  ║tPreHk)║      │
  src/gaia/          ╚════╤══════════╝ ╚══╤════╝  ╚════╤══╝      │
  environment/            │                │            │         │
  ┌───────────────┐        │           src/gaia/   src/gaia/  src/gaia/
  │[NEW]detector.py│       │           quality/    security/  session/
  │[NEW]feature_  │        │           ┌─────────┐ ┌────────┐ ┌──────────┐
  │   toggles.py  │        │           │[NEW]    │ │[NEW]   │ │[NEW]Stop │
  │[NEW]sync_     │        │           │Heuristic│ │PIISani-│ │Guard     │
  │   service.py  │        │           │FileScor-│ │tizer   │ │(HookEvent│
  └───────────────┘        │           │er       │ │(38 pat-│ │.STOP)    │
                           │           └─────────┘ │terns,  │ └──────────┘
  ╔══════════════════════════▼══╗           │       │6 cats) │
  ║  [NEW] src/gaia/orchestration/  ║           │       │[NEW]   │
  ║                               ║           │       │PIIAudit│
  ║  IntentOrchestrator           ║           │       │Logger  │
  ║   ├─ from_hook_context()      ║           │       └────────┘
  ║   └─ from_claude_code_session()║          │
  ║  ContextGatherer (git,files,env)║         │
  ║  ThinkingTriggerEngine [P2]   ║           │
  ║  AutoPilotDetector            ║           │
  ║  AutoPilotStateStore          ║           │
  ║  SelfPromptInjector           ║           │
  ╚═══════════════╤═══════════════╝           │
                  │                           │
  ╔═══════════════▼═══════════════════════════▼════════════════════╗
  ║                [EXISTS] src/gaia/hooks/                         ║
  ║  HookRegistry (event-based, priority, thread-safe)              ║
  ║  HookExecutor    BaseHook    HookContext    HookResult           ║
  ║                                                                  ║
  ║  HookEvent enum (17 existing + 2 new):                          ║
  ║  PIPELINE_START  PIPELINE_COMPLETE  PIPELINE_FAILED              ║
  ║  PIPELINE_CANCELLED  PHASE_ENTER  PHASE_EXIT                    ║
  ║  LOOP_START  LOOP_END  AGENT_SELECT  AGENT_EXECUTE              ║
  ║  AGENT_COMPLETE  QUALITY_EVAL  QUALITY_RESULT                   ║
  ║  DECISION_MAKE  DEFECT_EXTRACT  CONTEXT_INJECT  OUTPUT_PROCESS  ║
  ║  [NEW] PRE_COMPACT    [NEW] STOP                                ║
  ║                                                                  ║
  ║  Production Hooks:                    [NEW] Hooks:              ║
  ║  ContextInjectionHook                 CompactionPreHook         ║
  ║  OutputProcessingHook                 KnowledgeHarvestHook      ║
  ║  QualityGateHook (PHASE_EXIT)         (NOT modifying            ║
  ║  DefectExtractionHook                  ChronicleHarvestHook)    ║
  ║  ChronicleHarvestHook                                           ║
  ║  PipelineNotificationHook                                       ║
  ║  PreActionValidationHook                                        ║
  ║  PostActionValidationHook                                       ║
  ╚═══════════════════════════╤═══════════════════════════════════╝
                              │
  ╔═══════════════════════════▼═══════════════════════════════════╗
  ║              [EXISTS] src/gaia/pipeline/                       ║
  ║  PipelineEngine (async, integrates all components below)       ║
  ║  PipelineStateMachine  PipelineContext  PipelineState           ║
  ║  LoopManager  DecisionEngine  RoutingEngine                    ║
  ║  AuditLogger (hash-chain) ──[EXTEND]──► +THOUGHT +CONSENSUS   ║
  ║                             ──[EXTEND]──► +PIISanitizationFilter
  ║  DefectRemediationTracker  DefectRouter  DefectType            ║
  ║  RecursivePipelineTemplate (RECURSIVE_TEMPLATES dict)          ║
  ║  MetricsCollector  MetricsHooks  PhaseContract                 ║
  ╚═══════════════════════════╤═══════════════════════════════════╝
         ┌─────────────────────┼──────────────────────┐
         │                     │                      │
  ╔══════▼═══╗         ╔═══════▼══════╗      ╔════════▼═══════╗
  ║[EXISTS]  ║         ║[EXISTS]      ║      ║[NEW]            ║
  ║Quality   ║         ║Agents        ║      ║Session Layer    ║
  ║          ║         ║              ║      ║                 ║
  ║Scorer    ║         ║AgentRegistry ║      ║BuildSessionMgr  ║
  ║(27 cats) ║         ║(YAML+[NEW]   ║      ║BuildPlan        ║
  ║[NEW]     ║         ║ Markdown)    ║      ║SessionCmd Router║
  ║Heuristic ║         ║[NEW]         ║      ║StopGuard        ║
  ║FileScorer║         ║MarkdownScanner      ║                 ║
  ║validators║         ║HookInjector  ║      ║ RULE:           ║
  ║[EXTEND]  ║         ║MentionDetect ║      ║ session/ NEVER  ║
  ║security  ║         ║GlobalPoolSync║      ║ imports pipeline║
  ║validator ║         ╚══════════════╝      ╚════════════════╝
  ╚══════════╝
         │
  ╔══════▼═══════════════════════════════════════════════════════╗
  ║         [NEW] src/gaia/knowledge/                             ║
  ║  KnowledgeHarvester  StrategyStore  KnowledgeInsight         ║
  ║  ChromaDB collection: "gaia_strategy_knowledge"              ║
  ║  SEPARATE from gaia/rag/ which uses FAISS                    ║
  ╚══════════════════════════════════════════════════════════════╝
         │
  ╔══════▼═══════════════════════════════════════════════════════╗
  ║         [NEW] src/gaia/integrations/github/   [P2]           ║
  ║  GitHubEventPoller  GitHubEventBus  GitHubWebhookServer      ║
  ║  GitHubPipelineTrigger  PRQualityGateAdapter                 ║
  ╚══════════════════════════════════════════════════════════════╝
```

---

## 8. Critical Architecture Rules

The following rules MUST be enforced as code conventions and tests:

| # | Rule | Enforcement |
|---|------|-------------|
| 1 | `gaia/pipeline/` MUST NEVER import from `gaia/session/` | Import boundary lint rule or `tests/integration/test_session_pipeline_boundary.py` |
| 2 | `file_write_hooks.py` is a `__main__` script, NOT a `BaseHook` subclass | Code review gate; `BaseHook` subclasses must not have `if __name__ == "__main__"` |
| 3 | `StrategyStore` ChromaDB collection name must be prefixed `gaia_strategy_` | Unit test on collection initialization |
| 4 | `ChronicleHarvestHook` must NOT be modified to call `KnowledgeHarvester` — use the new `KnowledgeHarvestHook` instead | Code review gate |
| 5 | `IntentOrchestrator` must expose both `from_hook_context()` and `from_claude_code_session()` entry points | Interface test in unit tests |
| 6 | `chromadb` and `httpx`/`PyGithub` must be in `[project.optional-dependencies]`, NOT `[project.dependencies]` | `pyproject.toml` review |

---

## 9. Summary Counts

| Category | New Modules | New Classes | Existing Modules Extended |
|----------|-------------|-------------|--------------------------|
| Environment Layer | 3 | 4 | 0 |
| Security/PII Layer | 2 | 6 | 1 (`security_validators.py`) |
| Knowledge Layer | 3 | 5 | 0 |
| Orchestration Layer | 6 | 8 | 0 |
| Agent Extensions | 4 | 4 | 1 (`agents/registry.py`) |
| Quality Extensions | 2 | 2 | 0 |
| Session Layer | 4 | 6 | 0 |
| GitHub Integration | 5 | 6 | 0 |
| Hook/Audit Extensions | 0 | 1 (`KnowledgeHarvestHook`) | 3 (`base.py`, `audit_logger.py`, `quality_hooks.py`) |
| **TOTAL** | **29** | **42** | **5** |

---

## 10. What Is Already Done (No Action Required)

| Capability | GAIA Module | Notes |
|------------|-------------|-------|
| Pipeline orchestration | `src/gaia/pipeline/engine.py` | Exceeds Nexus's pipeline executor |
| Quality scoring | `src/gaia/quality/scorer.py` | 27 categories vs Nexus's heuristic scorer |
| Hook system | `src/gaia/hooks/` | More sophisticated than Nexus's hook dispatch |
| Agent routing by phase | `src/gaia/pipeline/routing_engine.py` | Exceeds Nexus's category router |
| Defect tracking | `src/gaia/pipeline/defect_remediation_tracker.py` | No Nexus equivalent |
| Decision engine | `src/gaia/pipeline/decision_engine.py` | No Nexus equivalent |
| Audit trail | `src/gaia/pipeline/audit_logger.py` | Needs PII extension (item 3.12) |
| Agent registry (YAML) | `src/gaia/agents/registry.py` | Needs markdown extension (item 3.8) |
| Document Q&A / RAG | `src/gaia/rag/sdk.py` | Entirely separate from knowledge harvesting |
| Metrics collection | `src/gaia/metrics/` | No Nexus equivalent |
| Loop management | `src/gaia/pipeline/loop_manager.py` | No Nexus equivalent |
| Recursive pipeline templates | `src/gaia/pipeline/recursive_template.py` | RECURSIVE_TEMPLATES dict already present |
