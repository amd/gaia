# Spike A - whatsapp-web.js prototype

Status: in-progress
Owner: engineering
Start date: 2026-05-03

Objective
- Build a minimal prototype using `whatsapp-web.js` to confirm: connection flow, message send/receive, media handling, and measure ban/instability signals over short runs.

Success criteria
- Can connect to a personal WhatsApp account and receive/send messages programmatically.
- Produce a short log of breakages, session invalidations, or bans over a 48–72h window (if feasible).
- Produce a brief operational risk note summarizing public ban-rate signals from maintainers and GitHub issues.

How to run the prototype (manual steps)
1. Prepare a dedicated test phone + WhatsApp account (not a primary personal account).
2. On a machine with Node.js (16+), run:

```bash
cd experiments/whatsapp-webjs
npm install
node index.js
```

3. Scan the QR code with the test phone (the script prints a QR to the terminal).
4. Send messages to the account and observe logs.

Initial run notes
- I started the prototype and it printed the QR to the terminal; you scanned it and the script accepted the handshake payload. The process was interrupted with Ctrl+C (exit code 130) during the manual run - see `experiments/whatsapp-webjs/run.log` for recorded events.
- Puppeteer required native Chromium libraries; on Debian/Ubuntu install `libnss3`, `libnspr4`, `libgtk-3-0`, etc. before running.

Next steps for the spike
- Keep the prototype running on a sacrificial test account for 24–72h and record any `disconnected`, `auth_failure`, or account ban signals in `run.log`.
- Collect public evidence: search maintainer issues and community threads for ban-rate anecdotes and link findings in this doc.

Notes and warnings
- This uses an unofficial client that automates WhatsApp Web; using it for automation is against WhatsApp's TOS and accounts do get banned. Use a sacrificial test account and do not use production / personal numbers.
- Keep runs short and document any account suspensions. Do not publish account credentials.

Privacy posture (explicit)
- Traffic flow: `whatsapp-web.js` runs a local WhatsApp Web client; message payloads and media are transmitted to Meta's WhatsApp infrastructure (WhatsApp Web). The library stores session credentials locally (e.g., in `LocalAuth`) so the host retains session keys, but message content is not kept purely local — it still transits Meta servers as normal WhatsApp traffic.
- Local storage: session tokens and any cached media are stored on the local host by the client. Local `run.log` files may contain message text unless logging is disabled or scrubbed.
- Recommendation: for spike runs, avoid logging full message bodies and treat logs as sensitive. Do not use customer or private phone numbers during testing.

Deliverables
- `experiments/whatsapp-webjs` prototype scaffolding (index.js, package.json).
- `docs/spikes/whatsapp-webjs.md` — this doc (updated with findings).
- Short report capturing observed instability and references to public threads.
