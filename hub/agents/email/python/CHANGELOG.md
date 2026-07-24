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

- **Draft/reply resolves a target from a sender or topic (#2403).**
  `draft_reply` no longer demands a concrete message id or the exact subject
  line. Its `message_id` argument now accepts a natural reference — a sender
  address (`rocm-ci@amd.com`), a topic/incident token (`SIC-4482`), or a subject
  keyword — and resolves it by searching the connected mailboxes and drafting
  against the best-matching thread. A concrete id (or one already tagged from
  triage/scan/read) still passes straight through (no search, no regression).
  Ambiguity fails LOUD with a candidate list to pick from, and no match fails
  LOUD with "not found" — never a silent wrong-target and never a bare
  "give me a message ID / exact subject" wall.
- **`/query` Lemonade-down errors are now actionable, not a raw traceback (#2139).**
  When the local LLM backend was unreachable, the `/query` SSE stream's terminal
  `error` event led with the raw `requests`/`urllib3` exception repr, giving the
  user no next step. The sidecar now classifies connection-shaped failures at the
  error boundary and emits the standard guidance — Lemonade Server not reachable at
  `<url>`; start it with `lemonade-server serve` (or `gaia init`); docs link —
  keeping the original exception appended as `Technical details:` for debugging.
  Every `/query` client (CLI, `gaia api`, third-party) benefits, not just the Agent
  UI relay (which mitigated host-side in #2136). Unrelated errors pass through
  verbatim, never masked behind a Lemonade message.

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
