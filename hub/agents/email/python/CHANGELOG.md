# Changelog — `gaia-agent-email`

All notable changes to the GAIA Email Triage agent package are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the REST
contract version is tracked separately as
`gaia_agent_email.contract.SCHEMA_VERSION` (see `CONTRACT.md`).

## [Unreleased]

### Added

- **`list_connected_mailboxes` tool — the agent can report live mailbox
  connection state (#2401).** "Which mailbox are you connected to?" now names
  the actual connected account(s) instead of paraphrasing the system prompt's
  capability text, and with nothing connected the agent says so plainly and
  points to Settings → Connectors. State is resolved live per call (via
  `available_mailbox_providers()` + `get_connection`), so a disconnect →
  reconnect made without restarting GAIA is reflected on the next question.
  The reactive fail-loud errors on mailbox *operations* are unchanged.

### Fixed

- **Two-turn "archive several… then undo" is now actually reachable (#2456).**
  "Undo that" with no id no longer demands the internal batch uuid:
  `undo_archive_batch` recalls the most recently archived, still-undoable
  batch from the persisted action log when none is supplied. The recall is
  DB-backed (`action_store.fetch_last_undoable_batch_id`), not an in-memory
  agent attribute — the sidecar builds a brand-new agent per `/v1/email/query`
  request, so anything kept only on the Python instance is gone before the
  very next turn even starts. Paired with the undo window already raised to a
  chat-speed 120s (#2447), a normal two-turn "archive several… then undo" flow
  now completes without the user ever seeing or typing a batch id, and it
  survives the real per-request agent boundary, not just a same-instance test.

- **Archive verifies it took effect, and same-day search finds today's mail (#2406).**
  Archiving now inspects the provider's post-mutation `INBOX` label and fails
  loudly instead of reporting a false success when the message is still in the
  inbox; and `after:today` / relative-day operators normalize to a
  timezone-robust `newer_than:1d` window so today's mail is reliably found. Both
  fixes apply on the REST surface (`/v1/email/archive`, `/v1/email/search`) as
  well as the agent's in-loop tools — a no-op archive returns an actionable 409,
  not a bare 500.
- **Draft/reply resolves a target from a sender or topic (#2403).**
  `draft_reply` no longer demands a concrete message id or the exact subject
  line. Its `message_id` argument now accepts a natural reference — a sender
  address (`rocm-ci@amd.com`), a topic/incident token (`SIC-4482`), or a subject
  keyword — and resolves it by searching the connected mailboxes and drafting
  against the best-matching thread. A concrete id (or one already tagged from
  triage/scan/read) still passes straight through (no search, no regression).
  Ambiguity fails LOUD with a candidate list to pick from, and no match fails
  LOUD with "not found" — never a silent wrong-target and never a bare
  "give me a message ID / exact subject" wall. The concrete-id probe only treats
  a genuine 404 (or an in-memory miss) as "not an id here"; a transient backend
  error (auth expiry, rate-limit, 5xx, network) on a valid id propagates instead
  of being masked as a misleading "no message found".
- **IMPORTANT / account-security mail is never auto-archived unattended (#2426).**
  At autonomy `full`, one cycle could auto-archive a provider-flagged IMPORTANT
  message (e.g. a Google security alert) the local model mislabeled as promotional.
  `TrustPolicy.decide` now applies a one-directional floor: an `archive` candidate
  that is Gmail-`IMPORTANT` / Outlook high-importance, or from a narrow set of
  account-security senders, is downgraded to a proposal at every level — a higher
  level or earned trust can widen what runs silently but can never override it.
  Ordinary promotional clutter still auto-archives.
- **Preferences persist without the embedder, and survive upgrade (#2427).**
  Priority/low-priority senders and category defaults now persist in the agent's
  `state.db` (like the trust ledger) instead of the embedding-backed MemoryStore,
  so they survive restarts even when the embedding model is absent. On first load
  after upgrade, a one-time read-through migrates any preferences a prior version
  wrote to the MemoryStore into `state.db` — nothing is silently dropped.
- **`/query` Lemonade-down errors are now actionable, not a raw traceback (#2139).**
  When the local LLM backend was unreachable, the `/query` SSE stream's terminal
  `error` event led with the raw `requests`/`urllib3` exception repr, giving the
  user no next step. The sidecar now classifies connection-shaped failures at the
  error boundary and emits the standard guidance — Lemonade Server not reachable at
  `<url>`; start it with `lemonade-server serve` (or `gaia init`); docs link —
  keeping the original exception appended as `Technical details:` for debugging.
  Every `/query` client (CLI, `gaia api`, third-party) benefits, not just the Agent
  UI relay (which mitigated host-side in #2136). Unrelated errors pass through
  verbatim, never masked behind a Lemonade message — including timeouts, which are
  deliberately not treated as Lemonade-down (a stopped local server refuses
  instantly; a timeout means up-but-slow, or a different host such as the Gmail
  backend, so it must not be relabelled "restart Lemonade").

- **`gaia email -q` surfaces the actionable Lemonade-down message instead of a
  generic "no final answer" (#2444).** When the agent loop handles a failure
  internally (Lemonade unreachable being the common case for the CLI) it sets an
  actionable `final_answer` and returns it *without* emitting an `answer` event,
  so the `/query` stream ended with no terminal event and the CLI fell back to
  "The agent finished without producing a final answer." The route now captures
  the loop's return value and surfaces that computed message as the terminal
  event — CLI↔Agent-UI parity on the Lemonade-down error copy.

- **Applying an existing label by its display name no longer fails with
  `Invalid label` (#2428).** `label_message` / `move_to_label` (and their batch
  variants) resolve a label's display name to its provider id via `list_labels`
  before calling the backend — mirroring the quarantine-label resolver. The model
  gets display names from `list_labels` and feeds them back into the apply call;
  Gmail's modify API addresses user labels by id (`Label_###`) and rejected the
  name, so the very label the agent had just enumerated as valid came back
  `Invalid label: <name>`. Passing a raw id still works; resolution is memoized
  per backend so a mixed Gmail+Outlook batch maps each message to its own
  provider's id; a name matching no existing label now fails with an actionable
  "here are your labels" error instead of Gmail's cryptic rejection.
- **Undo window default raised to 120s for chat-speed undo (#2447).** The
  archive/delete undo window default is now 120s, not 30s. The old 30s
  default was calibrated for an instant-UI-button undo; a chat-mediated bulk
  operation runs through the slower LLM tool-loop and could already exceed
  30s by the time it finished, leaving the "undo within the window" offer
  stale on arrival. Still overridable via `GAIA_EMAIL_UNDO_WINDOW_SECONDS`
  for deployments that need a different value.

- **Re-proposal dedup survives headless/scheduled teardown (#2381).**
  `record_proposal` wrote its dedup row through `query()`, which never commits,
  so when the scheduler rebuilt the agent between fires (closing the DB
  connection) the row was lost and the same still-in-inbox message was proposed
  again on every fire. The INSERT is now committed via `db.transaction()`, so a
  proposal recorded on one connection is visible after teardown/rebuild — matching
  the commit discipline already used by `record_outcome` and `record_autonomy_action`.

### Changed

- **Daemon-supervised scheduling (V2-15, #2156).** When the GAIA daemon spawns
  the sidecar it sets `GAIA_DAEMON_SUPERVISED=1`; in that mode the sidecar's two
  embedded clocks — the daily `BriefingScheduler` (#1918) and the one-shot
  `EmailJobScheduler` polling thread (#1919) — no longer start. The daemon owns
  a single reconciled clock and drives those jobs itself, so a scheduled brief
  or send now fires even with the web UI and CLI closed, and can no longer be
  silently killed when an idle sidecar is reaped.

  This is **additive and gated by supervision context, not a deletion**: a
  standalone `gaia-agent-email serve`, a bare integrator, or a
  `CustodyProvider` deployment never sees the env var and keeps both embedded
  clocks live exactly as before. The frozen `/v1/email/*` REST contract and
  `SCHEMA_VERSION` are unchanged.

### Added

- **Full autonomy — earn-trust engine + observe→decide→act loop (#1115, #557,
  #1483, #1287, #2005).** Set `autonomy_level` to `earn_trust` and the agent
  handles low-signal mail on its own: each heartbeat (`on_heartbeat` /
  `run_autonomy_cycle`) triages the inbox and either archives a message silently
  — where your explicit preferences sanction it, or its sender/category has crossed
  the trust bar in the ledger — or files a proposal for approval. Cautious on day one.
  - **The destructive floor always asks.** Send, forward, permanent-delete,
    RSVP, and quarantine require confirmation at *every* level, even for a
    fully-trusted sender — a parity test locks the policy floor to the agent's
    real `CONFIRMATION_REQUIRED_TOOLS`. Only reversible actions auto-execute,
    each with undo via `action_store`.
  - **It learns from your corrections.** `record_autonomy_outcome` is the single
    funnel every trust signal flows through; undoing an auto-archive (through the
    real `undo_archive_batch` tool) is captured automatically as a negative outcome
    and pulls trust back below the bar, updating both the sender and the category
    scope from one choice. Positive-outcome accrual — trust *rising* as suggestions
    are accepted or left standing — is not yet wired, so today the ledger only
    ratchets trust down.
  - **Inspectable, never a black box.** `autonomy_status()` and
    `GET /v1/email/agent/autonomy/{session_id}` expose the level, thresholds,
    and every earned-trust scope with its tally. `POST /v1/email/agent/autonomy`
    sets the level (pause / resume / `off` kill switch); `POST …/autonomy/run`
    triggers one cycle. Config knobs: `autonomy_level`,
    `autonomy_trust_min_samples`, `autonomy_trust_threshold`.
  - **Runs on a schedule.** `AutonomyScheduler` + `run_autonomy_job`
    (`autonomy_scheduler.py`) drive the cycle on an interval — off by default,
    opt in with `GAIA_EMAIL_AUTONOMY_ENABLED=true` (`…_LEVEL`, `…_INTERVAL_MINUTES`,
    `…_MAX_MESSAGES`). Mirrors the briefing scheduler and is gated off under
    daemon supervision, where the daemon's single clock drives `run_autonomy_job`
    instead — no second scheduler.
- `gaia_agent_email.supervision.is_daemon_supervised()` — detects the daemon
  supervision handshake (the env-var name is owned by core in
  `gaia.daemon.constants`, so daemon and sidecar can never drift).
- `gaia_agent_email.daemon_migration` — adapter that lifts the embedded clocks'
  jobs (pending `schedule_store` one-shots + the enabled daily briefing) into
  the daemon clock **exactly once** via the core reconciler's migration ledger,
  and asserts no job is silently dropped in the process.
