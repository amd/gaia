# Changelog — `gaia-agent-email`

All notable changes to the GAIA Email Triage agent package are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the REST
contract version is tracked separately as
`gaia_agent_email.contract.SCHEMA_VERSION` (see `CONTRACT.md`).

## [Unreleased]

### Added

- **The autonomy trust model can now be exercised end to end — broader candidates, an undo
  surface, and per-message decisions (#2529).** The proactive `earn_trust`/`full` loop's
  candidate generator (`_autonomy_candidate`) only ever proposed `archive`, so the rest of
  the declared reversible-action set, the nine-tool confirm floor, and the importance guard
  were unreachable and unverifiable from outside. Now: FYI mail maps to `mark_read` instead
  of `archive` (useful context stays visible, but no longer sits unread — PROMOTIONAL/spam
  mail is unaffected, it still archives); `_run_email_autonomy_cycle`'s report gains a
  `decisions` list — one entry per candidate considered (`message_id`, `tool`, `action`,
  `outcome`, `reason`, `sender`) — so a held-back decision explains itself instead of only
  being counted; and a new `EmailTriageAgent.undo_autonomy_action(action_id)` (exposed as
  `POST /v1/email/agent/autonomy/undo`) reverses any auto-executed action and records a
  negative outcome against its trust scope, generalizing the archive-only
  `undo_archive_batch` correction path via a new `organize_tools.undo_reversible_action_impl`
  and two pure `trust.py` functions (`record_autonomy_outcome`, `note_autonomy_undo`) that
  `EmailTriageAgent`'s existing methods now delegate to. The confirm floor is unchanged and
  still inviolable at every level — broadening the candidate map cannot make a floor tool
  auto-executable.

- **Agent-led mailbox onboarding — the agent sets up its own access, in the
  conversation (#2469).** Hitting the agent without a usable mailbox used to
  end the run with an error and a shell command
  (`gaia connectors connect google --scopes <scopes> --grant-agent
  installed:email`) — unactionable for anyone sitting in a terminal chat or a
  chat window. Two new tools replace it: `check_mailbox_access` classifies the
  state (`not_connected` / `reauth_required` / `connection_missing_scopes` /
  `agent_not_granted` / `ok`), and `setup_mailbox_access` walks the user
  through the fix, asking only for what it cannot determine itself. Each state
  opens with a **different** question, and the `agent_not_granted` case is
  repaired with a local grant write — no browser, no re-sign-in. Connecting
  Google still requires the user's own OAuth client ID and secret (GAIA ships
  no first-party client); the flow now explains that up front with a link and
  asks for the secret with a `sensitive` flag so surfaces mask it, instead of
  failing on a token refresh later. Detection is live per call, so a mailbox
  connected elsewhere (Agent UI, `gaia connectors`) means the agent stays quiet.

- **Mid-run questions on `/v1/email/query` — contract 2.5 → 2.6, additive
  (#2469).** The streaming agent loop could pause but never continue: a step
  needing user input emitted an event and then deliberately killed the run.
  Now a question emits the new **non-terminal** canonical SSE event
  `needs_input` — `{run_id, request_id, question, options[{value, label,
  description}], allow_free_text, sensitive?, respond_url, timeout_seconds?}` —
  and the run stays parked on the open stream until
  `POST /v1/email/query/{run_id}/respond` delivers the answer, at which point
  the SAME stream resumes. A stale or unknown `request_id` is rejected (409)
  rather than applied to whatever is pending; an unknown run is a 404; an
  unanswered question times out and the run ends with an `error` instead of
  hanging. The stream emits `:` heartbeat comments while parked so a client
  read-idle watchdog does not abandon it. `needs_confirmation` and its
  terminal, deny-by-default approval behaviour are deliberately unchanged
  (resolves `docs/spec/agent-ui-query-sse-contract.md` §9 Q3).

- **`list_connected_mailboxes` tool — the agent can report live mailbox
  connection state (#2401).** "Which mailbox are you connected to?" now names
  the actual connected account(s) instead of paraphrasing the system prompt's
  capability text, and with nothing connected the agent says so plainly and
  points to Settings → Connectors. State is resolved live per call (via
  `available_mailbox_providers()` + `get_connection`), so a disconnect →
  reconnect made without restarting GAIA is reflected on the next question.
  The reactive fail-loud errors on mailbox *operations* are unchanged.

### Fixed

- **`draft_reply` / `draft_forward` actually draft instead of asking for the
  text to draft (#2524).** Asked to draft a reply or forward, the agent
  correctly located the source message and then asked the user to supply the
  finished reply/forward text — the thing it was asked to write. Neither
  tool's docstring nor the base system prompt ever told the model that
  composing `body` is its own job; the only place that said so was the
  voice-profile style guidance, which only appears once enough Sent-mail
  history has been learned, so a fresh mailbox never saw it.
  `draft_forward`'s `body` was already optional, ruling out a simple
  required-parameter theory — this was a missing authorship contract, not a
  schema-required-ness problem. Both tools' docstrings and the always-present
  REPLYING/DRAFTING system-prompt section now say explicitly: the model
  writes the body itself, from the source message plus any stated
  constraints (length, tone, points to hit), in the same turn it resolves
  the target — and only uses the user's own wording verbatim when they hand
  it over explicitly. `send_draft` / `send_now` / `forward_message` remain
  confirmation-gated; drafting still never sends.
- **The inbox briefing carries a structured breakdown instead of one padded
  sentence (#2525).** `get_briefing` already returned the full
  `email_pre_scan` envelope (urgent/actionable messages, counts, applied
  preferences) — the tool's own docstring was the bug: it told the model to
  "write a short framing sentence, do not recite the JSON" as if a card
  rendered the details, but unlike `pre_scan_inbox` no card renders a
  briefing, so that one sentence was the entire answer. `summarize_briefing`
  now computes the breakdown in code (total scanned, urgency/category
  counts, the individual urgent/actionable messages, and named applied
  preferences) so the reply can never assert an urgency judgement the
  pre-scan classification did not itself make; the tool docstring and system
  prompt now point the model at that computed `data.summary` instead of
  asking it to compress everything away.
- **Snoozing/scheduling by ordinary phrases like "tomorrow morning" now
  actually works (#2526).** `schedule_send`/`snooze_message` used to hand
  relative-time phrases straight to a strict ISO-8601 parser, which failed
  and told the user in chat to supply ISO-8601 themselves — with an example
  timestamp that was already in the past. No scheduled job was ever created.
  The agent now resolves "tomorrow morning", "next monday", "in 3 hours",
  "this evening", "tomorrow at 7" (and similar) itself before calling the
  scheduling tools, anchored to the local time of the machine/process the
  agent runs on (the same convention naive ISO-8601 timestamps already used
  here — not UTC, not a per-user setting). A phrase that still can't be
  resolved fails with a proposed concrete time (tomorrow 09:00 local)
  instead of demanding a format. `cancel_scheduled_job` also now accepts a
  1-based position ("2", "second") from the most recently shown
  `list_scheduled_jobs` listing, since the user has no way to know the raw
  job id from chat.
- **`get_thread` returns every message in the right order — no more dropped
  or duplicated entries on a multi-participant thread (#2531).** Asked to
  list a full conversation chronologically, the agent could return the
  right message count but the wrong contents — one side of a two-party
  thread under-represented, entries duplicated, the last two messages
  swapped. Gmail's thread API does not guarantee message order, and
  `get_thread` — unlike its `summarize_thread` sibling, which already
  sorted defensively — trusted raw backend order and handed the model an
  unlabeled list to sort itself. `get_thread` now sorts by timestamp and
  numbers each message with its position (`index`/`of_total`), giving the
  model an authoritative order instead of one it has to compute.
- **"Show me my inbox" now works on a real mailbox with the default NPU
  profile (#2514).** `list_inbox` and `search_messages` capped each
  message's body independently but never checked the COMBINED size of the
  result — a realistic 25-message inbox built a >100KB tool response that
  overflowed the NPU profile's 32768-token context window on the very
  first tool call of a brand-new conversation, and `/clear` didn't help
  since nothing had accumulated yet. Worse, the overflow sometimes surfaced
  as a silently truncated message count (10 requested, 8 returned) rather
  than a clear error. Both tools now shrink every message's body together
  to fit the active device's context budget (GPU or NPU, whichever is
  running) — messages are never dropped to make the count fit, and a
  request too large even at the smallest usable body size fails with an
  actionable error naming the limit instead of silently returning less
  than was asked for.
- **Calendar listing and conflict checks no longer 400 on a date-only range,
  and never end a turn narrating a retry that didn't happen (#2517).**
  `list_calendar_events` and `detect_calendar_conflicts` forwarded a
  model-supplied bound like `2026-07-27` to Google verbatim; the live
  Calendar API rejects a date-only `timeMin`/`timeMax` with a 400, so "what's
  on my calendar the next 30 days" ran real tool calls and came back with no
  events. Both tools now normalize `time_min`/`time_max` (and
  `start_iso`/`end_iso`) to RFC 3339 before the request goes out — a bare
  date or naive datetime is coerced to UTC, an already-qualified timestamp
  passes through unchanged, and an unparseable bound raises an actionable
  error naming what was received instead of reaching the backend at all.
- **A trashed message is recoverable any time it's still in Trash, not just
  for a few seconds (#2523).** The only restore path (`restore_message`) was
  gated by a short undo window and a live `action_id`; once either was gone,
  the agent told the user the message was stuck, even though Gmail keeps
  Trash for 30 days. `restore_trashed_message` reconciles with the live
  mailbox state instead — no window, no id — and `search_trash` finds the
  message first when the id was never held onto. The `trash_message`
  confirmation now also says "moved to Trash", never "archived" — the two
  have very different recoverability and conflating them was its own hazard.
- **`permanent_delete` is no longer offered as a capability the agent doesn't
  actually have (#2533).** Real Gmail permanent delete requires a
  full-mailbox OAuth scope GAIA deliberately never requests (granting it
  would let every GAIA agent delete a user's entire mailbox for the sake of
  this one operation), so every call 403'd — yet asked directly, the agent
  claimed it could do it. The tool is no longer registered; the agent now
  says plainly it can move mail to Trash but not permanently delete it.
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
- **Batch archive/organize tools accept LLM-quoted, comma-joined ids (#2455).**
  Asking the agent to archive several inbox messages in one call ("Archive
  these three emails…") failed silently: the model emits its ids as a quoted,
  comma-joined string (`"id1","id2","id3"`), and `_coerce_ids` split on the
  comma without stripping the quotes, so Gmail rejected every id with "Invalid
  id value" and nothing was archived. `_coerce_ids` now strips surrounding
  quotes/brackets from every id — list or string, single id or batch — so the
  archive (and the other batch organize tools built on the same helper)
  succeeds.
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
