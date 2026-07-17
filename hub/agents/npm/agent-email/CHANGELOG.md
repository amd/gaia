# Changelog

What's new in `@amd-gaia/agent-email`, in plain language. For the technical detail
behind any entry — API shapes, endpoints, and version semantics — see
[`SPEC.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/npm/agent-email/SPEC.md).

## 0.6.0

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
