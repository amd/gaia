# Changelog

What's new in `@amd-gaia/agent-email`, in plain language. For the technical detail
behind any entry — API shapes, endpoints, and version semantics — see
[`SPEC.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.6.0/hub/agents/email/npm/SPEC.md).

## [Unreleased]

- **A request with no `Host` header is now rejected instead of served.** The
  sidecar's DNS-rebinding check used to skip itself when the header was absent.
  No normal client is affected — this package, the Python client, and curl all
  send `Host` — but a request that left it out used to get a `200` and now gets
  a `400`.

- **The published API contract now shows that requests need a session token.**
  The sidecar has always required a bearer token on most calls, but the
  contract document didn't say so. It now declares the requirement (and marks
  `/health`, `/version`, and the other exempt routes as public) — nothing about
  which calls need a token, or when, has changed (#2993).

## [0.6.0] - 2026-08-12

- **Work Microsoft 365 mailboxes are now supported alongside Gmail and personal
  Outlook.** A work/school Microsoft account (Entra ID) can now be connected and
  triaged the same way as Gmail or a personal Outlook.com mailbox — connecting,
  onboarding copy, and mailbox selection all recognize the new `microsoft_work`
  connector (#2629, schema 2.14).
- **Compatibility note:** if your app or its users refer to a mailbox as
  "office365", "o365", "m365", "microsoft 365", "entra", or "exchange", that
  now names the new work connector instead of personal Outlook. Before this
  release those words all pointed at the personal `microsoft` connector — the
  only Microsoft connector that existed. Someone with only a personal Outlook
  connected who uses one of these words is now told to connect the work
  mailbox instead of being served from their personal one. Plain `microsoft` /
  `outlook` / `outlook.com` / `hotmail` / `live` are unaffected.
- **`query()` can now carry a conversation forward.** `EmailQueryRequest`
  gains an optional `session_id`: set it once and reuse it on every turn of
  a conversation (e.g. `crypto.randomUUID()`), and the sidecar resolves the
  SAME agent each time instead of a throwaway one per call — so a
  follow-up referring to something an earlier turn surfaced has something
  to resolve against. Leave it unset and nothing changes (#2829, schema
  2.12).
- **A scoped "anything suspicious in my inbox?" question no longer dumps the
  full triage report (#2900).** `PreScanItem` gains `is_phishing`/`is_spam`
  (boolean, default `false`) — a flag previously readable only inside a
  prose `why` string is now a real field — and `EmailPreScanResult` gains
  `suspicious`/`suspicious_total` (schema 2.13): the phishing/spam-flagged
  subset of `actionable`, captured before its own cap so a flagged message
  ranked past it is never silently dropped from the count.
- **The agent's built-in skills ship switched off, so the whole context window
  goes back to your mail.** The six skills below are still in the package, but
  no set is active and none of them loads: nothing yet shows they make triage
  better, and an active set was consuming most of the room the agent had for
  bulk-triage results. A personal and a work mailbox get identical behaviour
  again, and `--skill-set` / `GAIA_EMAIL_SKILL_SET` now fail at startup saying
  there are no sets to pick rather than quietly doing nothing. Nothing else
  changes — same endpoints, same tools, same permissions.
- **One inbox triage card instead of two that disagreed.** Asking the agent
  to triage your inbox used to draw two summary boxes from two separate scans
  at different depths — one might say "nothing needs you" while the other,
  five lines below, listed a message needing review. The card is now one
  worklist (`needs_you`, schema 2.11) built from a single scan: up to five
  things that genuinely need you, each tagged with what to do (reply, decide,
  check, or a carried-over action item) and how old it is. `NeedsYouItem` /
  `BulkSummary` are new on `EmailPreScanResult` — `BulkSummary` carries a
  count plus the id(s) of the test(s) that filtered it, for an app that
  wants to render why a message didn't make the list, rather than a bare
  unauditable number; nothing existing was removed or renamed (#2743).
  `NeedsYouItem.detail` is also new — reserved for a couple of lines of
  real substance per row (the question actually asked, the meeting time
  actually proposed, the deadline actually quoted) — but ships **always
  empty** in this release: the
  per-item extraction pass that would fill it was implemented and then
  withdrawn before merge so it could ship on a firm timing budget rather
  than risk a slow scan; a follow-up will populate it.
- **Reconnecting your mailbox with no flags — the exact command GAIA's own
  error message told you to run — could silently wipe your permissions
  instead of fixing them.** A bare `gaia connectors connect google` (or the
  same reconnect from a first-time self-repair conversation) used to fall
  back to identity-only sign-in scopes whenever it wasn't told exactly what
  to ask for, overwriting a working mail-plus-calendar connection with
  nothing usable. That path now fails with a clear, copy-pasteable command
  instead of guessing, and every surface — the CLI, the Agent UI, this
  package's own connector setup, and the in-chat self-repair flow — now asks
  for the same scopes so none of them can quietly narrow what another one
  granted. Separately, calendar access is now clearly **optional**: a mailbox
  missing only calendar permission still triages, drafts, and sends normally,
  and calendar tools name the exact scope to add instead of taking the whole
  mailbox down with them (#2730).
- **The agent can now tell you which inbound mail is waiting on your reply —
  not just which of your own messages went unanswered.** Previously the agent
  could only flag sent mail nobody replied to; a colleague's "did you get a
  chance to look at this? can we meet Thursday?" was invisible to it. It now
  also flags inbound messages that ask directly for a reply, a decision, or a
  meeting time — but only when there's real corroboration that it's genuine
  correspondence (an existing back-and-forth in the thread, or a sender
  you've emailed before). A question mark or a convincing-looking sender name
  is deliberately not enough on its own — both show up constantly in
  marketing and cold-outreach mail, and a false "someone is waiting on you"
  costs more trust than a missed one.
- **Triggering an autonomy cycle while autonomy is switched off now tells you
  so, instead of quietly reporting nothing happened.** `POST
  /v1/email/agent/autonomy/run` used to return the same "nothing to do"
  response whether autonomy was disabled or had genuinely run and found
  nothing — there was no way to tell which. It now returns an error naming
  the current level and how to turn autonomy back on.
- **Asking the agent to draft a reply or forward now actually drafts one,
  instead of asking you to write it.** The agent would correctly find the
  right email, then ask you to supply the reply or forward text — the exact
  thing you'd asked it to write. Nothing told it that composing the message
  was its own job (that instruction only existed once it had learned your
  writing style from enough sent mail, so it never applied to a fresh
  mailbox). It now writes the reply or forward itself from the original
  message plus whatever you specified (length, tone, points to hit), and
  still uses your exact wording when you hand it over yourself. Sending is
  unchanged — every draft still needs your confirmation before it goes out
  (#2524).
- **Six built-in skills, and the groundwork for treating a personal mailbox
  differently from a work one — shipped switched off.** The skills (`personal`:
  inbox triage, newsletter digests, trip itineraries; `work`: inbox triage,
  meeting scheduling, action items, escalation) and the machinery that picks a
  set from the kind of Microsoft account you connected are in the package, but
  no set is declared, so none of it is active — see the first entry above.
  Turning it on is a change inside the agent; nothing in your integration
  changes either way (#2466).
- **Opt-in preview: small on-device models can now decide phishing flags and
  triage categories instead of keyword rules.** Turn it on with
  `GAIA_EMAIL_USE_SLM=true` on the sidecar (or `use_slm=True` in config).
  A compact classifier — running on the same local Lemonade server as the chat
  model, so nothing leaves the machine — makes the phishing call, and a second
  one labels the triage category, taking that decision away from the bigger LLM
  (which is still consulted for the spam verdict when the rules can't settle it).
  It is experimental, so it stays off unless you turn it on. If the
  models are unavailable for any reason, triage falls back to exactly the
  previous behavior. No API shape changed.
- **A trashed email is recoverable any time it's still in Trash — not just for
  a few seconds after you delete it.** The only way back used to be a short
  undo window right after trashing; miss it, and the agent told you the
  message was stuck, even though Gmail actually keeps Trash for 30 days. It
  can now find the message and restore it any time it's still there. The
  agent also stopped calling a trashed message "archived" in its confirmation
  — trash and archive recover differently, so it now says exactly what it did.
- **The agent no longer claims it can permanently delete email — because it
  can't.** Permanently deleting a Gmail message needs a scope GAIA
  deliberately never asks for (it would hand over delete access to your whole
  mailbox for one rare action), so every attempt failed. Asked directly, the
  agent used to say it could do it anyway. Now it says plainly it can only
  move mail to Trash.
- **Full autonomy now does more than archive, explains its decisions, and can be undone.**
  Previously the proactive `earn_trust`/`full` loop only ever archived low-signal mail —
  every other reversible action the trust model already declared (marking mail read,
  starring, labeling) was unreachable, the run report never said *why* a message was held
  back, and there was no way to undo an auto-executed action other than the archive-only
  `undo_archive_batch` tool. Now: FYI mail is marked read instead of archived (it stays
  visible, just no longer sits unread); `POST /v1/email/agent/autonomy/run` returns a new
  `decisions[]` field explaining every candidate's outcome and reason, including "held back
  for confirmation" and "held back — provider-flagged IMPORTANT"; and a new
  `POST /v1/email/agent/autonomy/undo` reverses any auto-executed action and records the
  correction against its trust scope, the same negative-feedback loop `undo_archive_batch`
  already gave archives. The destructive floor (send/forward/permanent-delete/RSVP/quarantine)
  is unaffected — it was already inviolable and stays that way at every level (#2529).
- **The agent sets up your mailbox itself, in the conversation.** Before, hitting
  the email agent without a working mailbox produced an error and a shell command
  to go run somewhere else — a dead end for anyone in a terminal or chat window.
  It now works out *which* of the four problems it actually has (nothing
  connected, credentials stopped working, a missing permission, or connected but
  not allowed for this agent), says something specific about that one, and offers
  to fix it right there. The connected-but-not-allowed case is fixed with no
  browser at all. Connecting Google still needs your own OAuth client ID and
  secret — the agent now tells you that up front with a link, instead of failing
  later (#2469).
  Integrators: `can_answer_questions` is only understood from 2.6 onward, so
  check `version()` before sending it — an older sidecar rejects the unknown
  field outright rather than ignoring it.
- **New: the agent can ask you a question mid-run** — schema 2.6, additive. A new
  non-terminal SSE event `needs_input` carries a question, 2-4 labelled options
  each with a description of what choosing it does, and a free-text escape;
  `respondToQuery(runId, requestId, value)` (`POST
  /v1/email/query/{run_id}/respond`) delivers the answer and the ORIGINAL stream
  resumes. An unanswered question ends the run with an error rather than hanging.
  Approvals (`needs_confirmation`) are unchanged: still terminal, still
  deny-by-default (#2469).
- **Work/school Outlook (Microsoft 365 / Entra ID) mailboxes now work, not just
  personal Outlook.com.** The Microsoft connector previously signed in only
  against the `consumers` tenant, so a corporate Microsoft 365 account was
  rejected before GAIA ever saw a token. It now uses the `common` tenant by
  default (both account types), overridable with `GAIA_MICROSOFT_TENANT`. A new
  zero-setup device-code sign-in connects without an Azure app registration or
  loopback redirect — from the CLI (`gaia connectors connect microsoft --device`)
  or the Agent UI (a **Sign in with a code** button on the Microsoft tile). No
  email-agent tool changed — the existing Outlook backend just reaches more
  mailboxes (#1275).
- **In the GAIA daemon deployment, the sidecar no longer holds long-lived OAuth
  secrets.** Previously a sidecar read the mailbox connection straight from the
  machine keyring. Now, under the Agent UI daemon, the daemon (the custody home)
  owns the refresh token and forwards only **short-lived access tokens** to a new
  sidecar intake (`POST /v1/connections/{provider}`, plus `GET`/`DELETE`) — the
  sidecar never sees the refresh token, the daemon re-forwards on expiry and
  withdraws on revocation, and only connectors **granted** to the email agent are
  forwarded. Added as sidecar contract **2.5** (additive over 2.4; every 2.4
  request/response shape is unchanged). This is **daemon-managed** — a standalone
  integrator using this package is unaffected and keeps resolving the mailbox from
  the local GAIA connector store exactly as before (#2154).
- **The agent's autonomy commands now work against the shipped binary.** `gaia
  email autonomy status/set-level/pause/resume/run/undo/kill/trust` call REST
  routes (`/v1/email/agent/autonomy*`) that did not exist in any previously
  published binary — a sidecar installed from 0.5.0 or earlier 404'd on every
  one of them, with nothing telling the caller why. All eight subcommands now
  reach a real route and get back a 200, or a correct 409 when autonomy is
  off (#2894).
- **Muting a sender no longer buries their genuinely urgent mail as
  promotional.** The category override for a muted (low-priority) sender was
  unconditional — every message from that sender was force-classified
  PROMOTIONAL regardless of content, which also made it an autonomy
  auto-archive candidate with no confirmation. "I don't care about most of
  this sender's mail" is not "this specific message is never urgent" —
  category is now always decided by content; muting only affects ordering
  (#2774).
- **Scanning a real Gmail inbox no longer fails outright on a rate limit.** A
  scan batching 100 messages in one request reliably tripped Gmail's
  per-user concurrency limit, and a single 429 discarded the other 99
  already-successful results with the whole scan failing on
  `CONNECTOR_ERROR`. Batches are now chunked to a measured-safe size, a 429
  is retried with backoff, and a message still rate-limited after retrying
  is dropped individually and reported — not thrown away with everything
  else (#2727).
- **A counting question about a long-bodied sender no longer overflows the
  model's context and comes back empty.** Searching messages defaulted to
  fetching full bodies, and a "how many emails from X in the last two
  weeks?" question against a verbose sender could blow the context window
  before the model produced an answer. The search now defaults to metadata
  only (subject/from/date/snippet, no body) — a counting or listing question
  never needed the body — cutting the result size by roughly an order of
  magnitude (#2782).
- **A fresh conversation's first inbox listing or search could overflow the
  NPU profile's context window before you got a reply.** `listInbox` /
  `searchMessages` capped each message's body independently but never
  checked the COMBINED size of the result — a realistic 25-message inbox
  built a response over the NPU profile's 32K-token budget on the very
  first call, and the overflow sometimes surfaced as a silently truncated
  count (10 requested, 8 returned) rather than an error. Both now shrink
  every message's body together to fit the active device's budget; a
  request too large even at the smallest usable body size fails with an
  actionable error naming the limit instead of quietly returning less than
  asked for (#2514).
- **Calendar answers can no longer invent attendee names or invite
  confirmations that aren't in the mailbox.** Asked "did anyone send me a
  meeting invite?", the agent could answer "yes" with no message,
  mutation, or attachment behind it — a real `organizer` field was
  sometimes narrated as "sent you an invite." Calendar listing and
  conflict checks now surface each event's real `attendees` (an event with
  none normalizes to `[]` instead of the field being omitted), and two new
  checks catch an invite or attendee claim the tool result doesn't support
  before it reaches you. Scoped to calendar attendee/invite claims only —
  not a general claim about hallucination elsewhere (#2766).
- **A reply, draft, or send could report failure even after it actually
  succeeded, and retrying made it worse.** A transient local bookkeeping
  write, unrelated to the real Gmail/Outlook call, could fail right after
  the message was actually sent or the draft actually created — and that
  bookkeeping failure was surfaced as if the whole action had failed.
  Retrying then hit an already-consumed draft id. `draft()`/`send()`/forward
  now report success whenever the real mail action succeeded regardless of
  that local write, and retrying an already-sent draft gets a plain "already
  sent" instead of a generic error (#2908).
- **The triage card is now assembled from the scan's own data, not retyped
  by the model.** The categorized breakdown the model used to compose
  freehand — numbering, message counts, addresses — could drift from the
  scan that produced it: a number pointing at the wrong message, an item
  repeated or dropped, or a bare item count with no list at all. The card is
  now rendered directly from the same `needs_you` data the scan already
  computed — a template fill, not a generation — so a reference like
  `archive 3` always names the message actually shown as 3; the model still
  writes the opening sentence and nothing else. On a 55-item real inbox this
  completed in under a minute end to end (#2858).
- **The launch secret no longer sits in the sidecar's environment.** The
  per-session auth token used to be handed to the sidecar as a bare environment
  variable, visible to any local process that can inspect process environments.
  A 0.6.0+ sidecar spawned by the GAIA daemon now receives it as an owner-only
  (`0600`) file that is removed when the sidecar stops; the env channel
  (`GAIA_EMAIL_SIDECAR_TOKEN`) keeps working for older binaries and for the npm
  lifecycle, exactly as before.
- **Asking "what's on my calendar?" no longer digs up years-old meetings.**
  Listing calendar events without a date range used to return the oldest
  instances of recurring series — events from years ago narrated as if they
  were this week. An unbounded listing now defaults to the next 30 days
  (starting now); passing explicit `time_min`/`time_max` bounds works exactly
  as before.
- **The plain-language agent loop is now part of the typed client.** 0.5.0's
  streaming endpoint required hand-rolled `fetch` + SSE parsing; now
  `client.query()` returns an async iterator of typed events (`status`, `token`,
  `tool_call`, `tool_result`, `needs_confirmation`, `final`, `error` — plus a
  visible `unknown` placeholder for event types added by a newer agent, never a
  silent drop), and `client.cancelQuery(runId)` stops a run mid-way. You mint
  `run_id`, so a run is cancellable from the instant you send it. A stream that
  breaks mid-run throws instead of looking like success.
- **The client now speaks contract 2.4.** `SCHEMA_VERSION` moved 2.3 → 2.4
  (additive — every 2.3 request/response shape is unchanged). The startup
  version handshake accepts any 2.x sidecar, so a 2.3-pinned client keeps
  working against a 2.4 sidecar exactly as before; only the new `query()` /
  `cancelQuery()` calls need a 2.4 (0.5.0+) agent binary.
- **On NPU-capable machines, triage now runs on the NPU by default.** When
  you haven't pinned a specific model, the agent checks whether the
  Lemonade Server it's talking to has an AMD NPU and the NPU-optimized
  model ready — if so, it uses that automatically for lower power draw;
  otherwise it keeps using the existing GPU/CPU model, exactly as before.
  `GET /v1/email/init` reports which one was picked. Accuracy/throughput
  numbers for the NPU model aren't published yet — that measurement lands
  in a follow-up release.

## 0.5.0

- **Ask the agent in plain language.** Send a free-form request ("find today's
  urgent mail and archive the promotions") to a new streaming endpoint and the
  agent works through it step by step with its tools, reporting progress as it
  goes; a run can be cancelled mid-way. Anything that would actually send mail
  still stops and routes you to the explicit draft-and-confirm flow. Not yet
  wrapped by the typed client — call the endpoint directly (see `SPEC.md`).
- **Iterate on the agent from source.** New `connectSidecar({ baseUrl })` attaches
  the client to a server you run yourself, and `gaia-agent-email serve --reload`
  (or `npx @amd-gaia/agent-email dev`) runs the agent's Python source with hot
  reload — so you can fix a triage/draft bug and re-test in seconds instead of
  waiting for a new binary. Additive — your existing calls are unchanged, and
  shipping to production just swaps `connectSidecar` for `startSidecar`. Exports
  the new `ConnectOptions` / `AttachedSidecar` types. Full walkthrough in
  `SPEC.md` → *Fast local iteration*.
- **Docs rewritten for humans.** The README, this changelog, and the evaluation
  guide now lead with what the agent does in plain language; the deep technical
  reference lives in `SPEC.md`.

## 0.4.0

- **Reply drafts come back as a ready-to-fill scaffold** (recipient + subject)
  instead of an always-empty body. Triage sorts and summarizes but doesn't write
  the reply text — so compose the body yourself and send it with `draft()` +
  `send()`.
- **The local agent now checks who's calling it.** Because it can send mail as you,
  it now requires a private per-session key that your app gets automatically — so
  another program on your machine, or a web page in your browser, can't quietly
  reach it to draft or send.
- **Draft in your own voice.** The agent can learn your writing style locally from
  your Sent mail (top greetings, sign-offs, typical length — never the raw
  content, and it stays on your device) and match it when drafting replies.
- **Better spam detection that works beyond Gmail.** Spam is now judged by the
  content itself, on-device, so it works for Outlook and any mailbox — not just
  Gmail's own spam label.
- **Follow-up tracking.** The agent can flag threads where you're still waiting on
  a reply past a window you choose (default 3 days), most overdue first. It points
  them out; it never sends a nudge for you.
- **Schedule a send or snooze a message.** Ask the agent to "send this tomorrow at
  9am" or push a message out of the inbox until a chosen time. Both are confirmed
  up front and can be cancelled before they fire.
- **Attachments.** Triage now sees attachments, and drafts and sends can include
  files (up to 25 MB each). When you confirm a send, the attachments are locked to
  what you approved — nothing can be swapped in or added after.
- **Action items become a task list.** Items pulled from an email are saved
  locally and linked back to the message, so re-triaging never creates duplicates.
- **Daily inbox briefing.** The agent can produce a morning inbox summary on a
  schedule with no prompt. Off by default; turn it on when you launch the agent.
- **A readiness check before your first triage.** Ask the agent whether the local
  model is actually up and get a clear yes/no with a hint on what to fix, instead
  of hitting an error on the first request.
- **Runtime memory toggle.** Turn the agent's memory (inbox profiling, learned
  preferences) on or off without restarting it.
- **Hold an ongoing conversation.** Beyond one-shot requests, the agent can be
  driven as a stateful, streaming chat over its local API — the same thing the
  GAIA Agent UI uses to power its email experience.

## 0.3.0

- **The eval score now measures what users feel.** Triage priority is ranked
  (urgent > needs-reply > FYI), so the score credits an exact *or* one-off bucket
  — a "needs-reply" called "urgent" is close, not a total miss. It measures 83.4 /
  100, and every release has to clear the bar to ship.
- **Triage many emails in one call.** New `triageBatch()` handles up to 100 emails
  or threads at once instead of one request each; each item succeeds or fails on
  its own, so check every result, not just the overall status.
- **Search your inbox, view your calendar, and file messages — through the
  package.** Read-only inbox search, calendar view/create/RSVP, and archive plus
  phishing-quarantine (both reversible within 30 seconds) are now available to
  apps embedding the agent, matching what the GAIA Agent UI can do.
- **Inbox pre-scan.** Get the triage card (urgent / needs-action /
  suggested-archive rows) for your recent inbox in one call.

## 0.2.5

Sending from a mailbox connected with view-only permissions now gives a clear
error naming the missing mail-send permission, instead of a confusing server
error. The playground's connect flow now asks for send access up front, so
connect → send just works.

## 0.2.4

First fully-published release of this feature set. Ships the per-platform agent
downloads plus this client. (The combined all-platforms download is temporarily
disabled — it exceeded a hosting size limit; the individual downloads work.)

## 0.2.3

Re-cut of 0.2.2 after a publishing-infrastructure fix — the first fully-published
release of this feature set.

## 0.2.2

Publishing-reliability fix so the download and npm publish complete. No change to
how the agent behaves.

## 0.2.1

- **One-command playground.** `npx @amd-gaia/agent-email playground` fetches the
  agent, starts it, and opens a browser page to try it — no setup.
- **Automatic cleanup.** The agent now shuts itself down when your app exits,
  crashes, or is interrupted, so it never lingers holding a port.

## 0.2.0

- **Browser-safe client.** A separate `@amd-gaia/agent-email/client` import works
  in a browser or Electron renderer (the main import stays Node-only, since it
  downloads and launches the agent).

## 0.1.0

- Initial release: the typed email client, the build-time downloader, and the
  helpers to launch and shut down the local agent.
