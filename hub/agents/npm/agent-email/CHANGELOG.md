# Changelog

All notable changes to `@amd-gaia/agent-email` are documented here. The package
follows [SemVer](https://semver.org/): the **MAJOR** of the on-the-wire
`SCHEMA_VERSION` is what `checkVersion` enforces at startup, so a contract MAJOR
bump is always at least a package MINOR bump with a migration note.

## Unreleased

Contract bumped to `SCHEMA_VERSION` **2.2** — additive over 2.1, so `checkVersion`
(MAJOR-only) keeps accepting existing clients.

### Added

- **Follow-up tracking (#1606).** The agent gains a read-only `check_followups`
  tool that scans the Sent folder of every connected mailbox and flags threads
  whose latest message is still the user's own outbound mail past a
  configurable window (default 3 days) — surfacing message id, recipient,
  subject, and age, most overdue first. Detection only: it never sends a
  nudge (any send stays confirmation-gated). The scan caps how many Sent
  messages it enumerates per mailbox (default 50, max 200); the result now
  also carries `scan_truncated: true` when a mailbox has more sent mail than
  that cap, so the caller knows older threads weren't checked. Agent-loop
  surface (chat / Agent UI / `gaia email`); the sidecar REST/MCP surface is
  unchanged and `SCHEMA_VERSION` stays `2.2`.
- **Attachment handling (#1542).** Real email has attachments; triage and replies
  that ignored them were incomplete. Read/triage now exposes attachment metadata —
  `EmailMessage` (request) and `EmailTriageResult` / `DraftReply` (response) carry
  an `attachments` array of `{ filename, mime_type, size_bytes, attachment_id? }`
  — and `draft` / `send` accept an `attachments` array of
  `{ filename, mime_type, content_base64 }` (standard base64, ≤ 25 MB decoded
  each) that reaches the mailbox as real MIME/Graph file attachments. The
  draft→send confirmation token binds each attachment's filename, MIME type, and
  content digest, so a confirmed payload can't have files swapped in or smuggled
  past the user. Fail-loud validation throughout: bad base64 / MIME / oversize →
  `422`, mismatched attachment set → `403`, Outlook > 3 MB (Graph simple-attach
  limit) → a loud error, never truncation. The agent's in-loop `draft_reply` /
  `send_now` tools gain an optional comma-separated `attachments` file-path
  parameter with the same fail-loud checks.
- **Scheduled send + snooze in the agent tool loop** (#1609): the agent can now
  schedule a send for a future time ("send this tomorrow at 9am") and snooze a
  message out of the inbox until a chosen time. A scheduled send is
  user-confirmed **at creation** (literal recipient/subject/body + fire time),
  stored as a regular mailbox draft plus a persistent one-shot job, and fired
  at/after its time by the agent's scheduler; snooze archives now and re-adds
  INBOX at the chosen time. Both are cancellable before they fire, and a firing
  failure is recorded on the job and logged — never silently swallowed.
  **Agent-loop only — no runtime/contract change**: no new REST endpoints, no
  new npm surface in this package (see SPEC "Agent-loop capabilities not on the
  contract").
- **Triage action items now persist as a task list** (#1605): `triage()` /
  `triageBatch()` write each extracted action item to the sidecar's local SQLite
  (`~/.gaia/email/state.db`), linked back to the source `message_id` (or
  `thread_id` for a thread) and de-duplicated per message on the normalized
  description — re-triaging a message never duplicates tasks. Side-effect only:
  the wire response, contract, and `SCHEMA_VERSION` are unchanged, and results
  without a `message_id` are not persisted. Read/complete surfaces arrive with
  GAIA's cross-agent task store (amd/gaia#1521); until then the store is the
  email-local `email_tasks` table.
- **Scheduled daily inbox briefing** (#1608): the sidecar can now run the inbox
  pre-scan on a daily timer — no prompt, no live caller — and expose the result on
  the new `GET /v1/email/briefing` (additive). The
  briefing payload is the same `email_pre_scan` envelope as `POST /v1/email/prescan`,
  produced by the agent's own `pre_scan_inbox` path, plus a `generated_at` stamp.
  **Off by default**: opt in by launching the sidecar with
  `GAIA_EMAIL_BRIEFING_ENABLED=true` (fire time `GAIA_EMAIL_BRIEFING_TIME`, 24h local
  `HH:MM`, default `08:00`; scan size `GAIA_EMAIL_BRIEFING_MAX_MESSAGES`, 1–100,
  default 25), e.g. via `startSidecar({ env: {...} })`. An invalid value fails sidecar
  startup loudly; the endpoint returns `404` until the first scheduled run. REST-only
  for now — no npm client wrapper method yet.

## 0.3.0

Contract bumped to `SCHEMA_VERSION` **2.1** — additive, no triage shape change, so
`checkVersion` (MAJOR-only) keeps accepting 2.0 clients.

- **Eval scorecard now measures acceptance, and the release gate enforces it** (#1437,
  #1894): the `SCORECARD.md` aggregate is now **within-one-bucket acceptance accuracy**
  (triage priority is ordinal, so exact-or-adjacent buckets are credited — what users
  feel) instead of exact 4-way match. Measured **0.834** (3-run mean on Strix Halo /
  Gemma-4-E4B, 95% CI [0.821, 0.847]) vs the **0.80** bar (#1437); exact 4-way stays a
  reported secondary (0.77). The card now carries run-to-run variance/CI, and the
  release gate enforces the 0.80 bar plus an anti-gaming URGENT-recall floor and a
  variance-aware regression check. No runtime/contract change — eval + packaging only.
- **Batch triage endpoint** (#1887): new `POST /v1/email/triage/batch` /
  `client.triageBatch(req)` beside the single-email endpoint, so a caller can triage
  up to 100 emails or threads in one request instead of one HTTP round-trip per
  message. The body carries an `items` array; the response carries a parallel
  `results` array, order-preserved, each entry holding exactly one of `result` or
  `error`. Per-item failures are isolated: HTTP 200 with every item errored is a
  valid response, so consumers MUST inspect each `results[].error`, not just the
  HTTP status. New npm surface: `client.triageBatch()`, the `BatchTriageRequest` /
  `BatchTriageResponse` / `BatchItemResult` / `BatchItemError` types, and the
  `MAX_BATCH_SIZE` constant. New MCP tool `triage_email_batch`. Purely additive —
  the single `triage()` / `POST /v1/email/triage` / MCP `triage_email` are unchanged.
- **Inbox search on the REST contract** (#1781): new read-only `POST /v1/email/search`
  / `client.search(req)`. The Agent UI lost live inbox search in the in-process
  agent rip-out (#1653); this restores it through the package. Lists messages
  matching a Gmail-style `query`/`labels` from the connected mailbox and returns
  metadata only (id, subject, sender, snippet, labels) — no body, no confirmation
  token. Needs a connected mailbox (`503` if none, `400` if 2+).
- **Archive + phishing-quarantine are now on the REST contract (#1779).** The Agent
  UI lost these in the #1653 in-process rip-out — they ran only in the agent loop.
  They're back **through the package**: `POST /v1/email/{archive,quarantine}` (and
  their `unarchive`/`unquarantine` reversals), plus `POST /v1/email/confirm` to mint
  the gate token. Both mutating actions are confirmation-token-gated exactly like
  `send` (a missing/invalid token → **403** before any backend call); both are
  reversible inside the 30s undo window (the reversal endpoints are ungated — they
  restore). Archive returns a `batch_id` undo handle and the `post_archive_id` so
  Outlook undo survives the folder-move id change (#1738). Quarantine is
  Gmail-only — an Outlook mailbox is refused with 400 rather than shipping a
  folder-move its label-based undo can't reverse. New client methods:
  `confirmAction`, `archive`, `unarchive`, `quarantine`, `unquarantine`.
  Contract `SCHEMA_VERSION` bumps `2.0` → `2.1` (additive — triage-shape unchanged).
- **Calendar surface on the REST contract (`SCHEMA_VERSION` 2.0 → 2.1, #1780).**
  Restores view / create / respond for calendar events through the packaged
  sidecar so the Agent UI gets it back without the in-process agent. New client
  methods: `listCalendarEvents`, `previewCalendarEvent`, `createCalendarEvent`,
  `respondToCalendarEvent` (+ `Calendar*` types). Create is confirmation-gated
  exactly like `send` — mint a token with `previewCalendarEvent`, echo it to
  `createCalendarEvent`; without a valid payload-bound token the create is
  rejected (403). Additive and same-major (2.x), so a 2.0 client keeps working.
- **Inbox pre-scan over REST** (#1778): new `prescan(req?)` client method →
  `POST /v1/email/prescan`. Reads recent inbox messages from the connected
  mailbox and returns the triage-card envelope (`kind: "email_pre_scan"` —
  urgent / actionable / suggested-archive rows + an informational count) the
  Agent UI's pre-scan card renders. Read-only; reuses the agent's own
  `pre_scan_inbox` classification path. Contract `SCHEMA_VERSION` → `2.1`.

**Verified before release:** the `darwin-arm64` binary was frozen with
`packaging/freeze.py`, passed the no-Python `smoke_test.py` (OpenAPI surface +
`/version` + `/v1/email/triage` all PASS), and was driven end-to-end through the
published `EmailClient`/`checkVersion` — single `triage()` and `triageBatch()`
both returned real, correctly-categorized Lemonade inference results. Full
transcript in the release PR.

## 0.2.5

Sending from a mailbox connected with identity-only scopes now returns an
actionable 4xx (naming the missing `installed:email` grant) instead of an opaque
HTTP 500, so the playground no longer points users at Lemonade for what is really
an OAuth-scope problem. The playground's mailbox connect now requests mail-send
scopes (so connect → send works), and the send panel keeps every connected
mailbox selectable while marking ones that lack mail-send access. No agent
wire-contract change — `SCHEMA_VERSION` stays `2.0`.

## 0.2.4

First fully-published release of this feature set. The whole-package zip download
(#1843) is temporarily disabled: the ~177 MB all-platforms zip exceeds Cloudflare's
edge upload limit, so the publish step rejected it (413) and blocked every prior
attempt (0.2.1–0.2.3). The worker-side streaming approach was reverted; 0.2.4 ships
the per-platform binaries + this npm client without the combined zip. Per-platform
binaries remain individually downloadable from the Hub. No agent wire-contract
change — `SCHEMA_VERSION` stays `2.0`.

## 0.2.3

Re-cut of 0.2.2 after the Agent Hub worker was redeployed with the large-artifact
streaming fix. 0.2.2 published its per-platform binaries but its whole-package zip
and npm publish never completed (the live worker hadn't yet picked up the fix);
0.2.3 is the first fully-published release of this feature set. No agent
wire-contract change — `SCHEMA_VERSION` stays `2.0`.

## 0.2.2

Release-reliability fix. The 0.2.1 tag published the per-platform binaries to the
Agent Hub but its npm publish never completed: the Hub Worker buffered the entire
upload in memory, so the new ~177 MB whole-package zip exceeded Cloudflare's
128 MB per-Worker memory limit and the publish step 502'd before npm. 0.2.2 fixes
that and is the first complete publish of the 0.2.1 feature set. No agent
wire-contract change — `SCHEMA_VERSION` stays `2.0` and the client + REST/MCP
surface are unchanged.

### Fixed

- **Whole-package zip + npm publish now complete.** The Agent Hub Worker
  (`POST /publish`) streams large `application/octet-stream` uploads straight to
  R2 with server-side SHA-256 verification instead of reading the whole body into
  memory, so the ~177 MB whole-package zip publishes without OOMing the Worker.

## 0.2.1

Adds the one-command `playground` launcher and automatic sidecar cleanup, and
makes this README the single canonical agent README (hub + npm). No wire-contract
change (`SCHEMA_VERSION` stays `2.0`).

### Added

- **`npx @amd-gaia/agent-email playground` — one-command launcher.** Fetches the
  binary, starts the sidecar, and opens the browser to `/v1/email/playground`,
  running until Ctrl+C. `--port <n>` to bind elsewhere, `--no-open` to skip the
  browser, `--out <dir>` to choose the binary cache. Makes "try the agent" a single
  command instead of fetch → spawn → find-the-URL.
- **Automatic sidecar cleanup (`autoCleanup`, default on).** `startSidecar` /
  `spawnSidecar` now reap the frozen sidecar's detached process tree when the host
  process exits, crashes (`uncaughtException` / `unhandledRejection`), or is
  interrupted (`SIGINT` / `SIGTERM` / `SIGHUP`) — so a skipped or missed
  `shutdown()` no longer leaves the binary running and holding its port. Pass
  `autoCleanup: false` to manage the lifecycle yourself; `shutdown()` stays the
  graceful, awaited path. A hard `SIGKILL` of the host process is the one case no
  in-process handler can catch.

### Changed

- **This README is now the single canonical agent README** (hub + npm). The
  release workflow publishes it to both, so the hub page and the npm listing no
  longer drift — the architecture diagram and the GAIA/npm install flow show up on
  `hub.amd-gaia.ai/hub/email`.
- **Architecture diagram loads from an in-repo raw image** instead of a
  version-pinned hub URL that the publish pipeline didn't populate, so it renders on
  GitHub and npm before the binaries are uploaded.
- **Agent version is shown on the hub cards** (listing + featured + detail).

## 0.2.0

### Changed (breaking)

- **Contract `SCHEMA_VERSION` 1.0 → 2.0.** The TS client and the frozen binary now
  agree on `"2.0"`. A 0.1.x client against a 2.0 binary (or vice-versa) fails loudly
  at `startSidecar` with `VersionMismatchError` — upgrade both together. No
  `expectedApiVersion` override is needed once client and binary match.

### Added

- **Browser-safe `./client` subpath export.** `import { EmailClient } from
  "@amd-gaia/agent-email/client"` exposes only zero-Node-dependency symbols, so the
  client bundles for a browser or Electron renderer. The `.` entry (binary fetch +
  sidecar spawn) stays Node-only.

## 0.1.0

- Initial release: typed `EmailClient`, build-time `fetch` CLI (download +
  SHA-256 verify), and sidecar lifecycle helpers (`spawnSidecar`, `waitForHealth`,
  `checkVersion`, `shutdown`, `startSidecar`).
