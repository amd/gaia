# Changelog — `gaia-agent-email`

All notable changes to the GAIA Email Triage agent package are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the REST
contract version is tracked separately as
`gaia_agent_email.contract.SCHEMA_VERSION` (see `CONTRACT.md`).

## [Unreleased]

### Added

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
