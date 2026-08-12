# Changelog — `gaia-agent-email`

All notable changes to the GAIA Email Triage agent package are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the REST
contract version is tracked separately as
`gaia_agent_email.contract.SCHEMA_VERSION` (see `CONTRACT.md`).

## [Unreleased]

### Added

- **Work Microsoft 365 mailboxes (#2629, schema 2.13).** A third mailbox
  provider, `microsoft_work` (work/school Entra ID, distinct from the personal
  `microsoft` Outlook.com connector), is now recognized wherever a provider
  string is accepted or returned — `REQUIRED_CONNECTORS`, the Graph token
  resolvers, onboarding, and mailbox selection all read it. Fixed alongside it:
  the daemon's OAuth token-forward path now forwards the work-mailbox
  connector's token to the agent, not just the personal one. `SCHEMA_VERSION`
  bumped `2.12` -> `2.13` (additive; existing `microsoft`/`google` consumers are
  unaffected).
- **A follow-up like "reply to number 1" can now resolve (#2829).** `POST
  /v1/email/query` accepts an optional `session_id`: send the same id on
  every turn of a conversation and the run resolves the SAME agent each
  time instead of a throwaway one per call, so a reference to something an
  earlier turn surfaced has something to resolve against. Omit it and
  nothing changes — this is additive (schema 2.12). Two turns can never run
  on the same session at once (a second call while one is in flight is
  rejected, `409`); a session id the sidecar has never seen arriving with
  prior conversation history (e.g. the sidecar restarted mid-conversation)
  gets a one-time notice instead of silently starting over.
### Changed

- **`office365`/`o365`/`m365`/"microsoft 365"/`entra`/`exchange` now resolve
  to the work connector, not the personal one (#2629, BREAKING).** Before
  this release these six words (`resolve_provider()`, used by tool-argument
  normalization and the mailbox-targeting NLU guard) mapped to the personal
  `microsoft` Outlook.com connector — the only Microsoft connector that
  existed. They now map to `microsoft_work`. A user who has only the
  personal connector configured and refers to their mailbox as "office365"
  or "exchange" now names a connector they have not connected, and gets the
  actionable not-connected error for `microsoft_work` instead of being
  served from `microsoft`. Bare `microsoft`/`outlook`/`outlook.com`/
  `hotmail`/`live` are unaffected.
- **Agent Skills ship disabled for this agent, pending eval evidence (#2695
  follow-up).** The `skill_sets:` and `default_skill_set: personal` blocks in
  `gaia-agent.yaml` are commented out, so `parse_manifest(...).skill_sets` is
  empty, `load_skill_set()` early-returns, and the agent launches with
  `loaded_skills == {}` / `active_skill_set is None`. Skills were turned on by
  default with no eval run behind them, and an active `personal` set cost ~1,334
  prompt tokens — shrinking the bulk-triage result envelope from 6144 to 4810
  (the `work` set, to 4070). `envelope_budget_tokens()` is back to **6144**
  (16384 − 9216 − 1024), byte-identical to pre-skills. Nothing was deleted: the six
  `gaia_agent_email/skills/<name>/SKILL.md` bodies still ship in the wheel and
  the frozen binary, and `SKILL_DIRS`, `select_skill_set()`,
  `ACCOUNT_TYPE_SKILL_SETS`, `GAIA_EMAIL_SKILL_SET`, `--skill-set`, and the
  `skill_prompt_tokens` accounting are all intact but inert. With no sets
  declared, a pinned set is a startup error rather than a silent no-op —
  `--skill-set personal` exits with "…requested skill set 'personal', but this
  agent declares no skill sets — Agent Skills are switched off in this build.
  Drop the option, or uncomment the 'skill_sets:' and 'default_skill_set:'
  blocks in gaia-agent.yaml." Re-enabling is uncommenting those two blocks
  **together**; a non-empty `skill_sets:` with no `default_skill_set:` is a
  validation error. `tools_count` (65), the REST/MCP contract, the connector
  surface, and `SCHEMA_VERSION` are unaffected.

### Fixed

- **A reply/draft/send action could report failure even though it actually
  succeeded, and a retry made things worse (#2902).** After a draft was
  created or a message sent, a separate local audit-log write (`state.db`,
  shared with the scheduler's background agent, #1115) could transiently
  fail with `database is locked` — and that bookkeeping failure was reported
  to the user as if the whole action had failed. `draft_reply`,
  `draft_forward`, and `send_draft` now match `send_now`'s existing
  ordering-invariant guard: the audit write is logged, not raised, once the
  real Gmail/Outlook call has already succeeded. Retrying `send_draft`
  against a draft id already consumed by a prior send now gets a plain
  "already sent" message instead of a generic connector-error dump.
- **Asked about upcoming meetings or calendar invites, the agent could invent
  attendee names and invite confirmations that exist nowhere in the mailbox
  or the tool trace (#2766).** A calendar event's real `organizer` was
  sometimes narrated as "sent you an invite" — a real field, misread — and a
  question like "did anyone send me a meeting invite?" could draw a
  confident "yes" with no mutation, message, or attachment behind it.
  `list_calendar_events` and `detect_calendar_conflicts` now surface each
  event's real `attendees` (Google omits the key entirely once an event has
  no one beyond the organizer; that's now normalized to `[]` instead of
  being discarded) so there is real data to ground an attendee claim
  against. Two new deterministic checks — the same "tool computes, model
  reports" pattern the calendar-conflict and attention-card checks already
  use — catch an invite claimed as sent/received when nothing this turn
  could have confirmed one, and an attendee named for an event the tool
  result shows has none; both leave a correctly-reported organizer and an
  honest "no attendees" alone.
- **A pinned `GAIA_EMAIL_SKILL_SET` leaked across tests, failing suites that
  never mention skills.** The variable was set process-wide by the skill-set
  tests and never cleared, so every later agent construction requested a set
  that no longer exists once skills ship disabled — surfacing as unrelated
  trash/restore, undo, and zero-connector failures rather than as a skills
  problem. The fixture now scopes and restores it, so the fail-loud
  `SkillSetError` fires only where a set is genuinely requested.
- **`get_thread` invented messages, senders, and timestamps that were never
  in the mailbox (#2765).** Asked to catch up on a conversation, the agent
  could hand back a duplicated message, another replaced by a repeat of an
  earlier one, a misattributed sender, and a timestamp that existed nowhere
  in the thread — even though `get_thread`'s own payload was already
  ordered, numbered, and carried each message's real `from`/`date` (#2531).
  The fabrication happened in the model's own free-composed prose, on top
  of a correct payload, so no docstring instruction alone could fix it. The
  registered `get_thread` tool now also returns a `kind: "table"` render
  card — the Agent UI's pre-existing generic primitive, no new client code
  — built directly from the same per-message fields the model reads, so
  the chat surface draws every sender and timestamp straight from the
  mailbox instead of from the model's reconstruction of it.
- **A low-priority-sender match no longer forces a message to PROMOTIONAL
  (#2666).** The mirror of #2632's priority-sender fix, for the direction
  that fix explicitly left alone: `_apply_session_preferences` used to
  override the heuristic/LLM/SLM's category outright the moment a sender
  matched the muted list, so a genuinely urgent message from a muted sender
  got buried as promotional — "I don't care about most of this sender's
  mail" is not "this specific message is never urgent." The preference now
  only tags `preference_applied` and updates the reason line for
  salience/ordering; category is always decided by content, in both
  directions. The `set_low_priority_sender` tool docstring (read by the
  model) and the `pre_scan_inbox` docstring's "derived from the low-priority
  bucket" claim are corrected to match.
- **A meeting proposal in a confidently-classified message vanished from the
  view the TUI renders on open (#2580).** `is_meeting_request` was wired into
  the scan by #2589, but the `needs_you` worklist (#2743, which replaced the
  #2582 `/attention` fetch as the TUI's on-open source) only kept the flag for
  messages already routed into `urgent`/`actionable` by category, and dropped
  it entirely for anything reaching `needs_review`. A message the category
  heuristic confidently calls FYI/PERSONAL — e.g. Gmail's own
  `CATEGORY_PERSONAL` label on a colleague's message — kept
  `is_meeting_request=True` from the scan but was silently counted only under
  `informational_count`, reproducing the original grounding incident
  ("Any chance to meet this Thursday at 9am?" reported under "0 actionable
  items") on current `main`. `is_meeting_request` now vetoes the
  informational/suggested-archives routing the same way an unconfident guess
  already does, and a meeting-flagged item keeps its `meeting_request` kind
  through `needs_review` instead of being downgraded to a generic
  "needs review" row.
- **`search_messages` stated a wrong, unstable count for a result set it
  received intact (#2756).** Asked "how many messages from X in the last two
  weeks", the agent ran the right query, got every matching row back, then
  reported a number that was both wrong and different on every run (measured:
  12 real messages stated as 6, then 4). The registered `search_messages`
  tool's merge layer now precomputes an exact `count` for the model to state
  verbatim, the same remedy `check_followups` already shipped for the
  identical failure (#2622). `truncated` is derived only from Gmail's real
  pagination cursor, never from `len(messages) == max_results` — a sender
  with exactly `max_results` matches and no further pages reports
  `truncated: false`, not a coincidentally-correct guess. `operator_retry`
  (computed by `search_messages_impl` since inception but silently dropped
  by the wrapper) now reaches the model too, so a broadened retry query is
  disclosed rather than presented as the user's literal search.
- **A reconnect with no explicit `--scopes` could silently overwrite a working
  mail+calendar connection with identity-only sign-in scopes (#2730).**
  `list(scopes) or list(provider.default_scopes)` at four `gaia.connectors`
  entry points meant an empty scope request against a provider that already
  had a connection or an agent grant silently fell back to
  `openid`/`email`/`profile` — including via the exact command GAIA's own
  error text told a user to run. That fallback is now rejected with an
  actionable error whenever prior state exists; a genuine first-time connect
  is unaffected. `connect_scopes()`'s own silent `except Exception` (reading
  the credentialed `OAuthProvider`'s `default_scopes`, unreachable before an
  OAuth client is configured — exactly the state a first-time self-repair
  runs in) now reads the catalog spec's `default_scopes` instead, which needs
  no credentials and removes the failure case rather than papering over it.
- **The daemon's forward-out mint was all-or-nothing: a connection missing
  even one optional (calendar) scope lost mail too (#2730).**
  `ConnectionForwarder.forward_provider` now mints against each agent's
  declared REQUIRED subset only (`scopes.py::REQUIRED_SCOPES` — mail only;
  calendar is requested at consent but never gates the mint), and reports the
  sidecar the intersection of the connection's real stored scopes with the
  ledger grant, never the ledger's raw claim (a shared connection widened by
  a different agent can no longer over-grant this one).

### Added

- **`pre_scan_inbox` now produces one triage worklist instead of two
  disagreeing summary boxes (schema 2.11, #2743).** Triaging the inbox used
  to draw a card from `pre_scan_inbox` and a separate, shallower attention
  card from the TUI's own on-open scan — one scan run twice at different
  depths, so the shallower box could confidently report "nothing needs you"
  while the deeper one listed a message needing review. `EmailPreScanResult`
  gains `needs_you` (`List[NeedsYouItem]`, capped at 5, ordered by kind then
  oldest-first) and `bulk` (`Optional[BulkSummary]`): a deterministic VIEW
  built ON TOP OF the already-classified `urgent`/`actionable`/`needs_review`
  buckets plus the waiting-on-you detector and persisted action items —
  never re-derived from raw scan results, so nothing those buckets already
  caught can go missing from it. `bulk.filter_tests` carries opaque ids (never
  prose) a renderer maps to a sentence, so "47 filtered" always names the
  test that filtered it. `NeedsYouItem.due_hint` (action items only) is
  wrapped in the same untrusted-input delimiters that cover a raw message
  body — it is regex-extracted verbatim from a message body and re-enters
  the calling agent's own tool-result context while that agent holds
  archive/send/delete authority. Nothing existing was removed, renamed, or
  relaxed from required to optional.
  `NeedsYouItem.detail` also ships on the wire, reserved for a couple of
  lines of real substance per surfaced item (the question actually asked,
  the meeting time actually proposed plus a COMPUTED calendar-conflict
  check — never a narrated verdict the tool didn't compute, the #2571
  precedent — or the deadline actually quoted), but is **always empty in
  this release**: the LLM extraction pass that would fill it, scoped to the
  agent-loop `pre_scan_inbox` tool call only, was implemented (commit
  `25738509`) and then withdrawn before merge so this change could ship on
  a firm timing budget rather than risk `pre_scan_inbox` timing out — up to
  five extractions at several seconds each is real latency `pre_scan_inbox`
  was never budgeted for. A follow-up will populate it, bounded to a
  deadline so a slow extraction degrades to partial detail rather than a
  stalled card.
- **The inbox-scan default is now owned in one place (#2743, closing the loop
  #2643 started).** `config.DEFAULT_INBOX_SCAN_MESSAGES` (50) is now the
  single source every scan-default call site imports instead of restating a
  literal — including `detect_waiting_on_you`'s own `max_inbox`, which
  previously carried its own, coincidentally-matching default.
- **`scopes.py`/`outlook_scopes.py` gained `REQUIRED_SCOPES` (mail only) —
  the request/enforce split (#2730).** `ALL_SCOPES` (mail + calendar) is what
  every connect path requests at consent; `REQUIRED_SCOPES` is the narrower
  subset the daemon's forward-out mint enforces. `mailbox_state.py` gained
  `requested_scopes()` alongside the existing `required_scopes()` gate, so
  the in-chat self-repair flow requests the same full union every other
  surface does instead of narrowing an existing connection on autonomous
  repair. A checked-in fixture
  (`tests/fixtures/connectors/email_scopes.json`) now guards both the Python
  and the TUI's Go scope lists against drifting apart.
- **Inbox scans go metadata-first, cutting per-message cost so the default scan size can
  rise from 25 to 50 (#2643).** Every scanned message used to cost one full-body fetch
  regardless of whether the heuristic ever read the body — `pre_scan_inbox` and the
  attention view never even wire an LLM classifier, so most of that body was fetched and
  never decoded. The scan now fetches metadata only (headers, labels, snippet — no body),
  runs the heuristic on that, and fetches a full body — batched, in as few round-trips as
  the mail backend supports — only for messages that actually need LLM follow-up. A
  `List-Unsubscribe` header (RFC 2369, arrives with the metadata fetch) is now a
  supplementary confident-bulk-mail signal for messages Gmail's own category labels miss.
  Classification is unchanged; a deadline/commitment signal in a bulk message's snippet
  still escalates to the LLM exactly as before. The classifier's own escalation body is
  also cut down to the sender's own new content (quoted reply chain and signature block
  stripped, reusing `voice_profile`'s existing quote/signoff detection) before it reaches
  the model — the one change here that affects what the LLM actually reads.
- **Meeting-request detection now runs during the inbox scan, not only on a message you
  point at directly (#2583).** `detect_meeting_request_heuristic` has existed for over a
  year but nothing ever called it from `triage_inbox`/`pre_scan_inbox` — a colleague
  proposing a time sailed through a scan uninspected. It now runs against every message's
  subject/snippet (no extra body fetch, no LLM call — the scan stays cheap) and the result
  is carried on `PreScanItem.is_meeting_request` for downstream rendering. Catching this
  also surfaced two real accuracy bugs in the heuristic itself: informal phrasing like "any
  chance to meet this Thursday at 9am?" previously scored a confident non-match (the noun
  list had "meeting" but not the verb "meet"), and the existing noun+time rule fired on any
  co-occurrence anywhere in the email — so marketing copy mentioning a "quick call" near an
  unrelated offer-deadline clock ("valid only through 4PM PT today") false-positived. Both
  are fixed; the noun and the time now have to appear within one clause of each other.
- **New read-only tool `list_waiting_on_you` (#2581): flags inbound mail awaiting
  the user's reply.** The inverse of `check_followups` (#1606) — that tool flags
  outbound mail nobody answered; this one flags inbound mail the user hasn't
  answered, e.g. a colleague's "did you get a chance to look at this? can we
  meet Thursday?". Qualification requires BOTH a genuine ask/meeting-time
  signal (`text_signals.has_direct_ask_signal` / `has_meeting_time_signal`,
  new dependency-free leaf module `tools/text_signals.py`) AND corroboration
  that the message sits in a thread with real back-and-forth already in it
  — sender shape and a bare `?` alone are not enough (measured against the
  adversarial PROMOTIONAL corpus: 47 of 104 rows carry a `?` from a
  non-automated-looking sender). Corroboration is scoped to the THIS
  thread's own history only: having emailed the same address before, in
  some other thread, does not corroborate anything (an earlier design that
  treated "ever corresponded with this address" as sufficient let a single
  genuine prior message to a vendor in a different thread corroborate every
  later marketing email from that address — sender identity was the wrong
  axis). Within a thread, a prior message merely existing is still not
  enough on its own: real correspondence needs more than one prior message
  FROM THE USER specifically (not the thread's total message count — an
  earlier version counted every message regardless of direction, so a
  vendor's cold intro plus a one-word "thanks" from the user hit the
  threshold and skipped the substance check), or one of the user's own
  messages with genuine substance (`text_signals.is_substantive_text`).
  A message the existing category heuristic confidently calls PROMOTIONAL
  never qualifies regardless of corroboration; and a sender the user has
  told to stop contacting them (`text_signals.is_opt_out_reply`,
  address-normalized so a plus-tagged variant can't dodge it) is suppressed
  unconditionally, since that is evidence of wanting less contact, not
  more. Bulk/automated senders are excluded via the existing
  `triage_heuristics._AUTOMATED_SENDER_KEYWORDS` list; already-replied
  threads are excluded; a meeting-signal check gates on
  `is_meeting_request and confidence == "high"`, never confidence alone.
  Read-only — no archive, label, star, draft, or send.
  Two known, accepted limitations: a PROMOTIONAL message sent into a
  thread that has already earned genuine corroboration can still qualify
  (closing this needs a message-level promotional judgement stronger than
  the existing label-driven heuristic — tightening corroboration further
  would only cost recall on real conversations); and prior messages are
  trusted by their backend-supplied `From` header with no authentication
  check, so a forged prior message could in principle contribute to
  corroboration (real spoofing defenses belong upstream, at the mail
  provider/backend level).


- **Preference removal and read-back tools — the agent can no longer claim
  it removed a preference it has no way to remove (#2520).** Asking the
  agent to remove a low-priority sender used to either do nothing while it
  reported success, or trigger the *set* tool instead and report success at
  adding when the user asked to remove — verified by diffing the agent's
  own `state.db` before and after. Three new tools (`remove_priority_sender`,
  `remove_low_priority_sender`, `remove_category_default`) pair with each
  existing `set_*` tool, and a new `get_preferences` tool reads back
  everything currently stored so a change is verifiable from the
  conversation. Every removal reports an explicit `removed` field — `false`
  means the preference was never set, and in that case the result carries no
  persistence claim at all, so the model has an unambiguous signal instead of
  inferring success from `ok: true` alone. Removing a low-priority sender
  never promotes it to priority (or vice versa) the way *setting* one
  deliberately clears the opposite flag — removal only ever touches its own
  target.
- **`gaia email autonomy` CLI (#2516).** A thin client over the session-scoped
  `/v1/email/agent/autonomy*` REST surface, relayed through the daemon like
  every other `gaia email` command (no second auth scheme): `status`,
  `set-level`, `pause`, `resume`, `run`, `trust`, `kill`. Closes the gap where
  the code and the plan doc both described this command before it existed.

### Fixed

- **A Gmail rate-limit no longer kills the whole scan (#2720, #2716).** Gmail
  enforces a per-user concurrent-request limit, and the metadata-first batch
  fetch (#2643) was oversized enough to reliably trip it — one 429'd
  sub-request discarded the other 99 already-successful results and the
  entire attention view/triage scan failed, surfacing a raw
  `CONNECTOR_ERROR: All connected mailboxes failed` (or, worse, the raw
  upstream JSON payload verbatim on the terminal). The batch subrequest
  ceiling is now 25 (measured against a live mailbox — 100 cold-429s, 25
  succeeds), a 429 is retried with bounded backoff (honouring `Retry-After`
  on the outer request), and a message that's still rate-limited after
  retrying is dropped individually — the attention view now shows every
  other message and reports the dropped one in `coverage.message_errors`,
  instead of failing the whole scan for everyone. `get_thread` (used by
  waiting-on-you detection on every scan) and the single-message fetch path
  get the same retry, not just the batch endpoint. The raw upstream error
  payload — previously interpolated with `repr()`, which could put terminal
  control/escape bytes on a user's screen — is now sanitized before it
  reaches any error message. The three tunables (batch size, retry
  attempts, backoff ceiling) are environment-overridable
  (`GAIA_EMAIL_GMAIL_BATCH_MAX_SUBREQUESTS`,
  `GAIA_EMAIL_GMAIL_RATE_LIMIT_MAX_ATTEMPTS`,
  `GAIA_EMAIL_GMAIL_RATE_LIMIT_MAX_BACKOFF_SECONDS`) so a bad value can be
  corrected without a new release.
- **A priority-sender match no longer forces a message to URGENT (#2632).**
  `_apply_session_preferences` used to override the heuristic/LLM's category
  outright the moment a sender matched the priority list — a Substack
  newsletter from a priority sender got promoted straight to URGENT even
  though the same decision's own reason line named Gmail's `CATEGORY_UPDATES`
  label as the (non-urgent) verdict. The preference now only tags
  `preference_applied` and updates the reason line for salience; category is
  always decided by content. The low-priority-sender branch (an explicit
  "downrank this sender" request) is unchanged.
- **A short, first-person human message proposing continued business no
  longer disappears into the informational tail (#2633).** The triage LLM
  prompt gained a disambiguation rule + worked example (paired with a hard
  negative so brevity alone doesn't now over-trigger `NEEDS_RESPONSE`) for
  messages like "Nice meeting you ... let me know what you think" that carry
  no explicit question mark or deadline but still warrant a reply.
  Independently, `pre_scan_inbox` gained an `include_informational` flag: the
  informational bucket was previously a bare count with no way to audit it
  ("95 informational, not listed") — passing the flag now returns the full
  id/sender/subject list for that count, at no extra scan cost.
- **The assistant no longer narrates things the current turn's own tools
  don't support (#2621, #2622, #2636, #2637).** Four related honesty
  defects, all guarded by one new mechanism: a mutation ("archived",
  "starred", "marked read", "moved to Trash", ...) was sometimes narrated
  as done with zero tool calls in that turn (observed 7 times in one long
  session, correlating with conversation length); `check_followups`
  reported fewer awaiting-reply items than its own intact result actually
  held, dropping a different subset on each of 3 fresh runs; a pre-scan's
  framing sentence could claim "no urgent or actionable items" while its
  own scan result carried non-empty urgent/actionable lists, or describe
  a per-INBOX-scoped `total_unread` as spanning "across your connected
  mailboxes"; and internal render/envelope scaffolding — a
  `[shown to the user]` context marker, `[suggested_archives]`-style
  envelope field names, raw provider message ids, undecoded `\uXXXX`
  escapes — occasionally leaked into user-facing prose instead of being
  summarized. New `gaia_agent_email.answer_grounding` module runs
  deterministic post-checks on the final answer text at the
  `process_query` output boundary: an ungrounded success claim or a
  claim contradicted by the turn's own tool result gets replaced with a
  grounded fallback, and scaffolding leaks get stripped in place.
  `check_followups` now also returns an explicit `count` field so nothing
  is left to miscount. The system prompt's pre-scan coverage note was
  rewritten to state `scanned`/`total_unread` as two separate facts
  rather than a "X of Y unread" fraction (matching the attention card's
  own wording), and to forbid the cross-mailbox phrasing outright. Also
  root-caused the unicode-escape leak: every `json.dumps` call in the
  shared agent loop that builds model-visible tool-result text was
  missing `ensure_ascii=False`, so non-ASCII characters in email subjects
  reached the model as literal escape sequences — as a side effect, this
  could also inflate a payload's measured length enough to trigger
  truncation it did not actually need. Fixed at every call site in
  `src/gaia/agents/base/agent.py`, benefiting every agent built on it, not
  just email.
- **Chat prose no longer contradicts the attention card already on screen
  (#2636, the other half of the fix above).** The bug as originally filed
  wasn't the pre-scan guard's territory: the attention card the Go TUI
  renders (`GET /v1/email/attention`, sections MEETING PROPOSALS/NEEDS
  REVIEW/ACTION ITEMS) could show real items while the same turn's answer
  said "no urgent or actionable items found" — because that view is never
  a tool call (`build_attention_view_impl` has no `@tool` wrapper; it only
  serves the TUI's on-open render), so the model generating the answer had
  no way to see it. `answer_grounding` now also reconciles the final
  answer against the same in-process cache the card was rendered from
  (extracted into a small new `attention_cache.py` so this stays possible
  without pulling FastAPI into the dependency-light grounding module), and
  appends — rather than replaces — a correction naming the card and its
  coverage when the two disagree. Declines to correct once that cache is
  older than its own freshness window (120s), so a card the user has since
  cleared can't get "corrected" back into looking unresolved.
- **`gaia email autonomy kill` now actually stops a scheduled cycle, not
  just a REST/CLI session's (#2649).** The scheduler builds a brand-new,
  stateless agent from environment variables on every fire and never
  touched the live agent object `set_autonomy_level` mutated — the gap
  #2624's fix explicitly called out as unresolved. `set_autonomy_level` now
  also writes a persisted kill flag into the same `state.db` every agent
  instance already shares (the trust ledger and session preferences do the
  same); `_run_email_autonomy_cycle` checks it once at cycle start (so a
  killed schedule stops hitting the mailbox at all) and again per message
  (so a kill landing mid-cycle still pre-empts an already-running scheduled
  run, the same way it already did for a REST/CLI session). Setting any
  other level clears the flag, so a resume un-blocks the scheduler too.
- **`gaia email autonomy run` now prints the error count and stop reason
  (#2651).** #2625 added `report["errors"]`/`report["stopped"]` to the
  autonomy cycle report, but the CLI's print function never read either
  field — a run that hit per-message failures printed the identical clean
  summary line as a fully successful one. It now prints `errors=<n>` on
  the summary line and, when the cycle stopped early, a second
  `stopped early: <reason>` line.
- **The agent no longer narrates its own calendar-conflict verdict — and
  gets it backwards (#2571).** Asked to list calendar events and flag
  conflicts, the agent listed events correctly, then stated a conflict
  conclusion it never computed: two events overlapping by 30 minutes were
  reported as "back-to-back and do not conflict." `detect_calendar_conflicts`
  was never called — only `list_calendar_events` ran, and the model judged
  overlap from the listed times itself. The tool was always correct; it
  simply never ran. `_SYSTEM_PROMPT` now has a CALENDAR CONFLICTS section
  mandating the tool for any conflict/overlap/double-booking question, both
  calendar tool docstrings state the same rule (the schema actually sent to
  the model), and a new deterministic guard in `calendar_tools.py`
  (`response_has_ungrounded_conflict_claim`) flags a conflict-judgement
  reply that never called `detect_calendar_conflicts` and appends a
  correction rather than letting the ungrounded verdict stand unqualified.
- **Inbox pre-scan now covers read mail, not just unread (#2638).** Pre-scan excluded
  read mail on a rationale that a later fix in the same issue (#2584) had already made
  moot — the coverage denominator moved to an exact `labels().get()` count independent
  of the listing query, so narrowing that query to unread-only bought nothing while
  making the single highest-value triage bucket (a message you opened but never
  answered) permanently invisible the moment you read it. Pre-scan now scans all of
  INBOX, matching the attention view and `list_waiting_on_you`, which never narrowed to
  unread in the first place. `total_inbox` (exact whole-INBOX count, sourced from the
  same call as the existing `total_unread`) is the new coverage denominator now that the
  scan isn't unread-only; schema bumped to `2.9`.
- **Thread summaries now keep the newest message's open asks (#2641).** A
  thread summary could reflect the opening question and an early reply while
  dropping the newest message entirely — even when that message carried the
  thread's only open ask and a concrete meeting proposal. Root cause: both
  `summarize_thread`'s system and user-turn prompts only ever guarded EARLY
  content ("do not drop a decision raised early..."); nothing asked the
  model to protect what is still open in the latest message. Both prompts
  now weigh the newest message's still-open asks equally, and a detected
  meeting proposal — from the existing deterministic
  `detect_meeting_request_heuristic`, run over the newest message's own
  decoded body, never the sender's raw matched text — is named from that
  signal rather than left to free-form generation. Thread summaries also get
  a larger length bound (`THREAD_SUMMARY_CHAR_LIMIT`, 700 vs. the
  single-message 300): several messages' decisions plus a new open ask plus
  a meeting time cannot fit in the single-message cap.
- **Mail-infrastructure banners no longer reach the summarizer as if they
  were the message (#2642).** A sensitivity marking or external-sender
  caution stamped at the top of a body sat exactly where a summarizer looks
  for "who said this" — on one real thread it was read as the author's name
  and attributed a colleague's statement to the banner text instead. New
  `gaia_agent_email.body_normalize.normalize_email_body` strips a small,
  enumerable set of known leading banners (never mid-message, never a body
  that merely discusses one) before `_thread_message_blocks` /
  `_format_message_for_llm` wrap the body for the model, with a hard cap on
  how much any single strip can remove so a banner with no trailing blank
  line can never take real content down with it. It also closes a
  pre-existing gap where an inbound body carrying a literal
  `<<<UNTRUSTED_EMAIL_BODY_END>>>`-shaped token was wrapped unscrubbed —
  that scrub previously ran only on LLM output, never on inbound text.
- **Fixed a data-loss bug in the #2642 banner stripper: it deleted real
  content on real (CRLF) mail.** `normalize_email_body`'s paragraph-break
  lookup only matched a bare `\n\n`, but an actual inbound body uses `\r\n`
  (RFC 5322) — so the lookup always returned "no blank line found," the
  strip fell back to its 300-char/5-newline removal cap, and that cap ate
  one or two real paragraphs past the banner instead of just the banner.
  Live testing against a real message caught this: the banner *and* the two
  paragraphs following it were removed. `_BLANK_LINE_RE` is now CRLF-tolerant
  (`\r?\n[ \t]*\r?\n`); the removal cap itself, the bounded scan window, and
  every existing hard-negative case are unchanged.
- **Banner stripping now reaches every path that builds a prompt from a raw
  body, including a banner's copies inside a quoted reply trail (#2647,
  #2653).** #2642 only protected the two thread/read rendering paths;
  `summarize_message`, the LLM triage follow-up, and meeting-request
  detection each built their own prompt straight from the decoded body, so
  a leading banner could still reach the model on those three. All three
  now call `normalize_email_body` before wrapping the body, same as the
  read paths. Separately, a live sweep found the bigger source of banner
  leakage: Outlook inlines the entire prior conversation into every reply,
  so a banner stripped from one message's own top-of-body still showed up
  a dozen times inside later replies' quoted trails — enough that one real
  thread summary named the banner text ("AMD General") as if it were a
  participant. `_thread_message_blocks` (used only by the two thread-SUMMARY
  renderers, never a raw-content display tool) now also drops the quoted
  trail via new `body_normalize.strip_quoted_trail` — reusing
  `voice_profile.strip_quoted_text`, with a fallback to the original body
  when a message's sole content is a quote, so a bare "+1" reply is never
  turned into an empty block. On a 10-message thread with full-history
  quoting this cut the transcript from 6,131 to 1,967 characters (68%
  smaller) as a side effect of removing the duplication.
- **The autonomy kill switch now pre-empts a cycle already running, instead
  of only affecting the next one (#2624).** A kill fired a second into a
  25-message run used to be confirmed as "off" while the run carried on and
  processed all 25 — the only enabled check read a `TrustPolicy` snapshot
  frozen before the loop started, so nothing inside it could see a kill
  fired mid-cycle. `_run_email_autonomy_cycle` now re-reads the live
  autonomy level immediately before each message's execute call and stops
  the batch there, recording why in the new `report["stopped"]` field
  (`"autonomy_off"`). Scope: this is pre-emptive for a cycle running through
  the REST/CLI session surface on a single-worker sidecar; the scheduler
  builds a stateless agent per fire from environment variables and is
  unaffected by a kill issued here (#2649). Killing one session
  also now stops every other live session in the process, since the caller's
  session id is not always the one an autonomy cycle happens to be running
  under.
- **A single per-message failure no longer discards the whole autonomy
  report (#2625).** A transient provider error used to propagate past the
  whole cycle, throwing away the record of every message already archived
  or marked read for real — the caller got a bare 500 and no way to tell
  what had actually changed short of querying the database by hand. The
  cycle now catches a per-message failure, records it in the new
  `report["errors"]` (exception type plus a redacted, length-capped
  message — auth headers, tokens, and email addresses are stripped, never
  the raw provider payload), and continues to the next
  message — stopping only after 3 CONSECUTIVE failures (resets on any
  success) so a systemic outage doesn't grind through the whole batch
  logging one identical error per message. A bookkeeping-call failure
  (recording the action for undo, clearing the re-proposal guard) that
  happens *after* a message was already mutated is logged but never
  reclassifies that message as failed. A cycle-level failure (triage
  itself raising) still propagates, unchanged.
- **The triage scan now actually follows pagination, and `scan_truncated`
  tells the truth (#2634).** Raising the scan's `max_messages` above one
  provider page used to do nothing — `triage_inbox_impl` issued a single
  `list_messages` call and never followed the returned `nextPageToken`, so
  asking for 500 messages still returned 100. Worse, the attention view's
  `scan_truncated` was computed as `len(results) >= max_messages`, which
  flips to "not truncated" the moment a request exceeds one page of real
  mail — exactly when the scan is least complete. The scan now pages until
  `max_messages` is collected or the mailbox is exhausted, de-duplicating
  message ids across pages and clamping the accumulator client-side (Outlook's
  continuation ignores `max_results` entirely). `scan_truncated` is now
  derived solely from whether the last-fetched page's own cursor says more
  mail exists, never from comparing request/response length — a mailbox
  whose size exactly equals the request now correctly reports no truncation,
  instead of the length-only formula's false positive.
- **A slow credential-store read no longer takes the whole sidecar down.**
  `GET /v1/email/connectors` read the OS credential store directly on the
  asyncio event loop, and that read has no bounded worst case — on macOS it can
  sit in `SecItemCopyMatching` waiting on an authorization decision a background
  process never receives, and a corrupted or contended store can stall it too.
  On the loop, a stall like that costs the whole process rather than one
  request: nothing else can be scheduled until it returns, so every route stops
  answering, `/health` included, while the process stays alive and its
  supervisor goes on reporting it "running". Seen in the field on one machine
  and captured with a stack sample of the parked loop. The read now runs off the
  loop, so however long the credential store takes, the rest of the sidecar
  keeps answering.

- **`POST /autonomy/run` refuses instead of silently no-oping while autonomy
  is `off` (#2528).** Previously the route returned HTTP 200 with the same
  empty-report shape whether autonomy was disabled or had genuinely run and
  found nothing to do — a caller could not tell the two apart. It now returns
  **409**, naming the current level and how to change it.
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
- **Bundled Agent Skills, and the active set keyed to the mailbox kind (#2466).**
  **Ships disabled** — the manifest blocks are commented out, so none of the
  behaviour below is active; see *Changed* above. The rest of this entry
  describes what the machinery does once they are uncommented.
  The agent brought identical instincts to every mailbox — the same triage
  judgement for one full of newsletters and booking confirmations as for one full
  of meeting invites and outstanding commitments. It now bundles six
  instruction-only skills under `gaia_agent_email/skills/<name>/SKILL.md` —
  `inbox-triage`, `newsletter-digest`, `travel-itinerary`, `meeting-scheduling`,
  `action-item-extraction`, `escalation-routing` — and `gaia-agent.yaml` groups
  them into two sets: `personal` (triage + newsletters + travel) and `work`
  (triage + meetings + action items + escalation), with `inbox-triage` in both
  because sets overlap rather than partition. Exactly one set is active per
  launch. `EmailTriageAgent.select_skill_set()` maps the connected mailbox's
  account type onto a set — the kind GAIA now derives from the Microsoft
  `id_token` `tid` claim at connect time — and `--skill-set` /
  `GAIA_EMAIL_SKILL_SET` (`EmailAgentConfig.skill_set`) override it outright,
  while `GAIA_EMAIL_ACCOUNT_TYPE` (`EmailAgentConfig.account_type`) pins the kind
  instead. A Gmail-only mailbox has no equivalent claim, so its kind is genuinely
  unknown and the manifest's `default_skill_set: personal` applies — which does
  mean a *work Gmail* mailbox lands on the personal set, by declared default and
  with a log line, not by anything inferring the kind from the mailbox; pin
  `GAIA_EMAIL_ACCOUNT_TYPE=work` for that case. An undeclared set name raises
  naming the valid sets rather than falling back. The skills declare
  no `tools:` and no `permissions:`, so `tools_count` stays 59 and the REST/MCP
  contract, connector surface, and `SCHEMA_VERSION` are all unchanged; relocating
  the agent's tool implementations into skills is separate work (#2672).
- **On-device SLM classifiers for phishing and triage category (experimental,
  `use_slm=False` by default).** Two compact embedding classifiers
  (`specific-ai-tools`, served by the same local Lemonade server as the chat
  model) can run ahead of the LLM. Phishing is decided by the SLM alone when it
  answers — the keyword/domain heuristic is not consulted for that message —
  and the triage SLM decides the category whenever the heuristic is not
  confident. The LLM classify call is skipped only when the heuristic already
  settled `is_spam`; otherwise it still runs for the spam verdict and its
  category answer is discarded. Every SLM path fails safe: an unreachable
  server, a failed model pull, a prediction error, or a label outside the
  taxonomy falls back to the existing heuristic + LLM flow, so the previous
  behavior is the floor, not the risk. Enable with `use_slm=True` or
  `GAIA_EMAIL_USE_SLM=true` — the `slm_triage_*` / `slm_phishing_*` model +
  checkpoint pairs on `EmailAgentConfig` ship preconfigured. A half-configured
  pair fails `validate()` loudly, and an unparseable `GAIA_EMAIL_USE_SLM` raises
  instead of silently defaulting to off.

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

- **`POST /autonomy/run` and `/autonomy/undo` no longer collapse an actionable
  connector error into a bare, textless HTTP 500 (#2617).** Both routes let a
  `ConnectorsError` from mailbox I/O (e.g. no forwarded credential) escape
  unhandled, so FastAPI turned the agent's own detailed message — what's
  missing and the exact `gaia connectors connect ...` command to fix it —
  into a plain "Internal Server Error" with no body, which the CLI then
  reduced further to the string `HTTP 500`. Both routes now catch
  `ConnectorsError` and re-raise as an `HTTPException` carrying the real
  message, mapped to status per the same table used by the Agent UI's
  connectors router (`ConfigurationError` → 503, `AuthRequiredError` →
  401/403, any other `ConnectorsError`, the observed case, stays 500 — now
  with a body). `gaia email autonomy run` no longer prints the same error
  twice (`log.error` + `print`) either — one line on stderr, with the log
  record still available under `--logging-level debug`.
  **Follow-up:** the "no mailbox connected yet" cold-start error is a
  *different* `ConfigurationError` class (`gaia_agent_email.config`, not
  `gaia.connectors.errors`) that shares no base class with `ConnectorsError`
  — it still escaped `/autonomy/run` as a textless 500. Now caught
  explicitly and mapped to 503, same as its connectors-side namesake.

### Fixed

- **A batch-tool retry no longer gets killed mid-recovery by the streaming
  layer (#2515).** When the model called a batch tool with a spurious extra
  argument (e.g. `archive_message_batch` with a stray `mailbox` kwarg), the
  agent loop correctly rejected it and started retrying — but the SSE layer
  couldn't tell that per-tool error apart from a genuinely fatal failure, so
  it ended the response and cancelled the still-retrying agent, dead-ending
  the turn with no answer and no stats line. `print_error` now carries a
  `recoverable` flag through to the wire; a recoverable error folds to a
  non-terminal status line instead of a terminal `error`, so the retry can
  reach completion and the user still sees the failure as it happens.
- **A failed memory startup is now visible in chat, and blames the right
  cause (#2519).** When the embedding model wasn't reachable, memory quietly
  disabled itself: a log line and a REST field said so, but the agent's
  answers made it look like a missing feature ("I don't have a tool to view
  saved preferences") rather than a broken one. The agent now prints a
  startup warning naming the real problem and the fix. It also used to blame
  every failure on `GAIA_MEMORY_DISABLED=1` or a stopped Lemonade — the
  common real case is neither: Lemonade is running fine but the embedding
  model was never pulled. The message now tells those two apart and gives
  the matching remedy (pull the model vs. start Lemonade), since acting on
  the wrong one wastes the user's time.
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
