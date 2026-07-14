# Changelog

What's new in `@amd-gaia/agent-email`, in plain language. For the technical detail
behind any entry — API shapes, endpoints, and version semantics — see
[`SPEC.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.4.0/hub/agents/npm/agent-email/SPEC.md).

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
- **Conversational agent surface.** The agent can now be driven as a stateful,
  streaming conversation over its local API — the same thing the GAIA Agent UI
  uses to power its email experience.

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
