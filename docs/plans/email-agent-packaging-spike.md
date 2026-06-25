# Email Agent Packaging - Phase 6 Spike

This doc records the spike decision and actionable follow-ups for integrating the packaged Sidekick email agent (`hub/agents/python/email`) into the Agent UI without changing the agent's logic.

**Scope**: map capabilities → packaged interface → UI surface; decide among Options A/B/C (or hybrid); inventory in-process glue to remove; propose sequenced follow-up issues; call out key tests (token keyring race, draft/send token flow).

**Outcome (short)**: hybrid approach - ship Inbox (A) on frozen REST immediately, preserve full conversational UX by proxying the packaged agent loop over the packaged `pipe/cli` surface (C), and add a small, targeted contract extension (B) for long-running / batch ops (pre-scan, batch-archive, calendar, search) via new REST or MCP endpoints with a contract version bump. This minimizes immediate UX regression, keeps the agent logic unchanged, and scopes contract changes to a small set of additive endpoints that are independently reviewable.

**Rationale**: the frozen REST already covers triage/draft/send; shipping an Inbox view (A) unlocks immediate dogfood and limits risk. Preserving conversational chat requires exposing the agent loop - the package already exposes `pipe/cli` so proxying it is lower cost than reauthoring the loop into REST. Batch/long-running operations are best served by explicit endpoints (REST or MCP tools) so the UI can orchestrate progress / cancel / undo; those require a contract version bump and re-freeze.

**Capability → Packaged-Interface → UI Surface**

- **Triage (per-message)**: **Packaged Interface**: REST sidecar `/v1/email/triage` (frozen) - **UI Surface**: Inbox view (cards) with fields: category, is_phishing, summary, suggested_action, action_items. (Action buttons call draft/send endpoints.)
- **Per-email summary**: **Interface**: REST `triage.summary` -- **UI**: MessageCard summary panel and message hover/peek.
- **Draft reply**: **Interface**: REST `/v1/email/draft` (single-use confirmation token) + `/v1/email/send` - **UI**: Draft composer embedded in Inbox card (thread-aware). UI must persist/propagate draft token across compose/send flow.
- **Phishing detection**: **Interface**: REST `triage.is_phishing` - **UI**: prominent phishing banner in MessageCard and inbox filter.
- **Full-thread comprehension**: **Interface**: REST `triage` accepts `ThreadInput` - **UI**: Thread view (Inbox → open thread) with thread-level summary and per-message breakdown.
- **Batch archiving**: **Interface**: NOT exposed by frozen REST/MCP today - **Recommended Interface**: Add REST `/v1/email/batch_archive` or MCP `organize_tools.archive_thread_batch` (see tradeoffs) - **UI**: Inbox bulk-select + archive CTA with progress + undo.

Agent-loop-only features (tools in `gaia_agent_email/tools/*`):
- **Inbox pre-scan** (pre-scan_inbox): currently agent-loop-only (long-running). Recommended: exposed as REST `/v1/email/pre_scan` (returns a job id + SSE/progress) or as an MCP streaming tool. **UI**: Inbox pre-scan card / initial onboarding CTA.
- **Quarantine + undo**: agent-only today. Recommended: REST `/v1/email/quarantine` + `/v1/email/quarantine/undo` (idempotent). **UI**: Quarantine panel, undo snackbar.
- **Calendar detect / create / conflict resolution**: agent-only. Recommended: add MCP `calendar_tools` or REST `POST /v1/email/calendar/create` returning calendar event id. **UI**: Suggest meeting CTA inline, Calendar pane.
- **Search (server-side)**: agent-only. Recommended: REST `/v1/email/search` with typed query → UI: Search pane integrated in Inbox.
- **Preferences / memory / profile tools**: agent-only. Recommended: REST `/v1/email/preferences` (read/write) or MCP tool. **UI**: Settings tab in Email view.

Interface assignment summary (recommended):
- Use existing frozen REST for immediate launches (triage, summary, draft, send, health, version).
- Use packaged `pipe/cli` (agent loop) proxied by the UI process for conversational chat (no agent logic change). Implement a small proxy daemon in the npm client / UI process that spawns the packaged binary and tunnels stdin/stdout over WebSocket to the frontend.
- Add a minimal set of additive REST/MCP endpoints for long-running or batch ops (pre-scan, batch_archive, calendar, search, quarantine, preferences). These require a contract version bump (see note below on where the contract version lives) and a short re-freeze cycle.

API versioning and re-freeze impact

- Any added endpoints or changed request/response shapes must be additive. Bump the packaged agent's contract version by updating `API_VERSION` (which aliases `SCHEMA_VERSION`) in `hub/agents/python/email/gaia_agent_email/version.py` (the single source of truth for the REST contract version; currently `2.0`). `SCHEMA_VERSION` is defined in `hub/agents/python/email/gaia_agent_email/contract.py`. The agent's `/v1/email/version` route serves this value as `apiVersion`. Note: `gaia-agent.yaml`'s `version` is the package build/version (e.g. `0.2.2`) and is distinct from the REST contract version; do not conflate them. The UI must detect and fail-loud on contract mismatches.
- Re-freeze impact: packaging pipeline will need to produce a new frozen binary + npm thin client update. Keep changes scoped to a few endpoints to minimize freeze time.

Send/draft flow note

- `/v1/email/draft` currently returns a single-use confirmation token required by `/v1/email/send`. The UI must thread this token through compose/send and persist it during transient disconnects. For long-running drafts consider adding a draft-save endpoint if UX requires it (out of scope for immediate swap).

In-process wiring to remove (confirmed on `main`)

- **Remove**: UI in-process agent construction + mail session-specific providers: `src/gaia/ui/_chat_helpers.py` - `_session_mail_provider` and factory call sites.
- **Remove**: tool→SSE render mapping for pre-scan: `src/gaia/ui/sse_handler.py` entries `_RENDER_TOOL_TO_LANG["pre_scan_inbox"]` and `_capture_render_payload` wiring.
- **Remove**: email-specific session special-casing in `src/gaia/ui/routers/sessions.py` (for example, `mail_provider`-driven model rebuild or other email-only session hooks). Do **not** remove the generic `evict_session_agent` lifecycle hook used by sessions across agents.

Keep (transport-agnostic UI/DB pieces)

- `mail_provider` session column/models/routers: `src/gaia/ui/database.py` (mail_provider column), `src/gaia/ui/models.py`, `src/gaia/ui/routers/sessions.py` (session persistence behavior) — keep as-is.
- Connector grant migration and formatting helpers: `src/gaia/connectors/grants.py`, `src/gaia/connectors/formatting.py`.
- Frontend rendering components: `EmailPreScanCard`, `EmailConnectCta`, `MessageBubble` — keep and wire to new Inbox view.

Key cross-process token caveat (keyring)

- The packaged sidecar will run out-of-process and must read connector tokens from the OS keyring. There is a known refresh-race risk when multiple processes update tokens concurrently. Test step (must be included in follow-ups):
  1. Validate sidecar reads tokens from the same keyring APIs the backend uses (libsecret / keyring lib). Confirm consistency for the platform(s).
  2. Simulate concurrent refresh: spawn UI process + packaged sidecar, trigger token refresh from UI and sidecar concurrently; assert no stale-token race or lost refresh. If race exists, add optimistic lock / refresh coordination protocol (e.g., DB-backed token version + compare-and-swap, or sidecar owning refresh with IPC proxying).

Sequenced follow-up issues (suggested, each scoped to one PR)

1. Transport swap — REST triage
- Title: `email-ui: route triage/draft/send to packaged sidecar REST`.
- Scope: UI calls swap from in-process to calling packaged `/v1/email/triage|draft|send`; keep identical payload shapes; wire draft token through composer. Tests: triage round-trip via npm client smoke script.

2. Inbox UI screen
- Title: `ui: add Inbox/Triage view and MessageCard components`.
- Scope: Add Sidebar entry, Inbox view, per-message cards mapped to `triage` response, draft composer, phishing banner, thread open. Tests: unit + integration UI smoke.

3. Chat proxy (preserve conversational UX)
- Title: `email: proxy agent-loop from packaged binary via pipe → websocket`.
- Scope: Implement small proxy that spawns packaged agent binary (npm client helper exists), tunnels stdin/stdout to WebSocket for the frontend, preserves agent-loop semantics. This uses existing `cli: true`/`pipe: true` packaging (no agent logic changes). Tests: end-to-end chat round-trip to packaged binary.

4. Batch & long-running ops contract extension
- Title: `email: add pre_scan / batch_archive / calendar / search endpoints to packaged sidecar`.
- Scope: Add additive endpoints (REST or MCP), bump the packaged agent contract version (update `API_VERSION`/`SCHEMA_VERSION`), coordinate freeze. Keep each endpoint as its own PR (pre_scan, batch_archive, calendar, search). Tests: smoke endpoints + SSE/progress where applicable.

5. Token keyring coordination test & mitigation
- Title: `email: validate sidecar keyring refresh coordination`.
- Scope: Cross-platform test harness that validates concurrent refresh scenarios; if flaky, implement small token coordination RPC or recommend sidecar-owned refresh.

6. Remove in-process glue
- Title: `email-ui: remove in-process email agent wiring`.
- Scope: Remove `_session_mail_provider` construction, SSE pre-scan mapping, special eviction handling; ensure tests and UI use packaged surfaces. Run CI, manual dogfood.

7. Documentation & packaging
- Title: `docs: document email sidecar contract/API version, migration guide`.
- Scope: Release notes, UI migration steps, new APIVersion details for integrators.

Proof-of-concept (optional)

- Run the npm thin-client to spawn the packaged sidecar and call `/v1/email/triage`. Capture the example request/response and paste into the Phase 1 PR. This confirms triage payloads are usable by the UI.

Acceptance checkboxes

- [x] Capability→interface→UI table produced.
- [x] Decision recorded (hybrid: A immediate + C proxy + selective B extensions).
- [x] In-process wiring inventory listed and linked.
- [x] Token keyring race called out as a test step.
- [x] Sequenced follow-up issues listed and scoped.

Next steps for implementer

- Approve the hybrid decision (tag @kovtcharov-amd for architecture sign-off).
- Create the follow-up issues in GitHub using the titles/scopes above. Start with (1) Transport swap + (2) Inbox UI to dogfood quickly.

---
Filed-by: spike (design) - no code changes in this spike.
