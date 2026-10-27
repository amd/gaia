# Admin console — adoption, engineering health, and privacy-preserving telemetry

Implementation plan for [#1821](https://github.com/amd/gaia/issues/1821).

> **Status: Phases 1–3 built** in [`workers/admin`](../../workers/admin/README.md) (Users, Developers, CI and Evals pages, nightly D1 snapshots, Cloudflare Access sign-in). Not yet built: release-quality signals and alerts (Phases 6–7).
>
> **Deferred:** app telemetry and user-initiated error reports (Phases 4, 4b and the telemetry panels of 5). They raise consent and legal questions we have chosen not to take on yet. Their design is kept below so it can be picked up later; nothing built depends on them.

## Goal

One auth-gated page that answers: is GAIA being adopted, do people keep using it,
do installs and releases work, and is engineering healthy — with history, so we see
trends rather than snapshots. The built dashboard uses public data only; the deferred
telemetry design below would be on by default, minimal, fully disclosed, and easy to turn off.

## Decisions to settle first

| # | Decision | Recommendation |
|---|----------|----------------|
| 1 | Installer downloads come from **GitHub Releases**, not R2 | Treat GitHub `download_count` as the Tier 1 source; #1819/#1821 R2 wording is stale. R2 stays only for hub agent packages if they still use it. |
| 2 | GitHub gives cumulative per-asset counts, no IPs | "Unique installers (dedup by IP)" is impossible from GitHub. Headline = daily **download delta** from snapshots; "unique installers" comes only from app telemetry. |
| 3 | `/admin` needs a server runtime; `website/` is static Astro served from Railway | New `workers/admin` Worker with a D1 binding. `workers/website-router` already fronts `amd-gaia.ai/*` and carves out `/docs`; add an `/admin` carve-out to it the same way, with a Cloudflare Access application on that path. The existing `workers/agent-hub` Worker has only R2 and keeps its role. |
| 4 | Snapshot store | D1 (SQL, easy time series) over R2 JSON. One row per day per metric. |
| 5 | Telemetry scope | App-wide (CLI, Agent UI, TUI, daemon), not hub-agents only. #1820 is hub-only today and must be widened or superseded. |
| 7 | Error reports go to a private store, not the public dashboard API | Team-only access via Cloudflare Access, 90-day retention, deletable by report id. |
| 8 | Who emits telemetry | One emitter in the Python backend/daemon, with one schema. The Agent UI, Go TUI and CLI only render consent and settings through the backend's HTTP API; the TUI must not reimplement or shell out. |
| 9 | Where public writes land | A separate `workers/telemetry` Worker (rate-limited, write-only) sharing the D1 database, so the public write path and the private admin read path never share code or credentials. |
| 6 | Telemetry default is **on** (opt-out) for the **Basic** tier only; **Extended** is opt-in, reversing the opt-in default in #1820 and the zero-telemetry stance in #890 | Decided by the maintainer. Needs the public privacy messaging updated and a legal/privacy review of regions that require opt-in consent (e.g. EU/EEA) before release. |

## Architecture

```
GitHub API (releases, Actions, issues)  ─┐
npm / PyPI download APIs                 ├─> Worker cron (nightly) ─> D1 snapshots ─┐
Actions artifacts (JUnit, scorecards)   ─┘                                          ├─> Admin Worker (/admin + /api)
GAIA app (telemetry) ─> POST /v1/telemetry ─> validate against schema ─> D1 aggregates ┘        ▲
                                                                                      Cloudflare Access (SSO)
```

## Phases

Each phase ships independently and is useful alone.

### Phase 0 — Spec and decisions
- Resolve the five decisions above; update #1819/#1820/#1821 to match.
- Write the telemetry event schema and privacy doc (`docs/guides/privacy.mdx`) before any client code.
- Done when: decisions recorded on the issues and the schema is reviewed.

### Phase 1 — Public-data collector and snapshots (no telemetry)
- Worker cron pulls, once a day: per-release and per-asset `download_count` (including RCs), stars, forks, open issues/PRs by label, npm and PyPI counts.
- Store as `(date, metric, dimension, value)` rows in D1. Download deltas computed on read.
- Backfill what the APIs allow (stars history, Actions runs ~90 days). Everything else starts accumulating on day one.
- Missed runs are stored as absent, never as zero.
- Tests: collector against recorded API fixtures; delta math across a missed day; pagination; API error surfaces loudly.

### Phase 2 — Admin shell and access control
- Worker route `/admin`, gated by Cloudflare Access; the API validates the Access JWT itself (defence in depth).
- Views: overview tiles with sparklines and deltas, and a time-range picker (7/30/90/all, kept in the URL).
- **Downloads panel (admin only; the public site shows no download counts per release):**
  - *Over time:* daily downloads (delta of the cumulative GitHub counts) as a line, with release and RC markers on the axis and a 7-day average; total across all releases and a stacked view split by release.
  - *Per release:* table of every release including RCs, with total downloads, downloads in the first 24 h / 7 d / 30 d after publish (so releases of different ages compare fairly), and share of the total. RCs are visually distinct and can be filtered out.
  - *Per asset:* breakdown by installer or platform (Windows, Linux x64/ARM64, macOS, standalone binaries) within a release, and by version.
  - Handles deleted assets or releases (a negative delta is shown as a gap and flagged, not clipped) and states plainly that GitHub counts include automation and updater fetches.
- Charts follow the dataviz skill; empty state is "no data yet", never `0`.
- Tests: unauthenticated and invalid-JWT requests rejected; seeded-data rendering; range picker.

### Phase 3 — Engineering health (CI)
- Ingest Actions runs and jobs daily: pass rate per workflow on `main`, p50/p95 duration, queue time per runner pool, flaky-job detection (same SHA fails then passes), top failing jobs.
- Ingest test and eval artifacts: tests total/pass/fail/skip, newly failing or skipped tests, eval pass rate per category vs. the committed baseline and the 80% target, PASS→FAIL / FAIL→PASS flips.
- PR flow: time to first review, time to merge, merge-queue wait. Release pipeline: RC→final lead time, publish success.
- Prerequisite check: confirm the workflows upload JUnit/scorecard artifacts; add uploads where missing.
- Tests: parser tests on real artifact samples; flaky detector on constructed run histories.

### Phase 4 — Privacy-preserving telemetry (client + ingestion) — DEFERRED

**Principles**
- **On by default, easy to turn off.** The installer (desktop installer and `gaia init`) shows what is sent, in plain language, with telemetry pre-selected and a clear option to disable it right there. Silent installs use the default (on) and print the same disclosure and the opt-out command.
- **Also a setting** in the Agent UI and TUI settings and in `gaia privacy`, changeable any time.
- **Existing installs** are never switched on silently: on upgrade the user sees a one-time notice with the full list and the off switch, and nothing is sent until that notice has been shown.
- Off and silent in CI, non-interactive test runs, and when `GAIA_TELEMETRY=0` or `DO_NOT_TRACK=1`. These always win over the default.
- Minimal by design: only adoption and error/bug signals, never what the user asked or what the agent answered.
- `gaia privacy show` prints status, the install id, and the exact payloads queued; `gaia privacy off` and the settings toggle revoke; `gaia privacy reset-id` rotates the id.
- Fire-and-forget: telemetry never blocks, slows, or fails a user action. Failures are debug-logged only.

**What is sent: a closed allowlist, enforced client-side by a typed schema (enums and bounded numbers, no free-form strings). Two tiers.**

*Basic — on by default (the only thing a default install sends):*

| Event | Fields |
|-------|--------|
| `app_start` | gaia version, OS family, arch, hardware class (npu/gpu/cpu), install channel. At most once per day. |
| `install_outcome` | agent or component id, version, `ok` / `hash-mismatch` / `net-fail` / `other` |
| `error` | component (e.g. lemonade, installer, agent, ui), error class from a fixed enum, GAIA version, count bucket. No message text, no stack trace, no paths |
| `update` | from-version, to-version, ok/error |

*Extended — off until the user turns it on (own switch in the installer and settings, with its own exact list):*

| Event | Fields |
|-------|--------|
| `funnel` | `first_launch`, `lemonade_ready`, `first_chat`, `first_document` (each fires once) |
| `agent_run` | agent id, ok/error, error class (enum) |
| `feature_used` | feature or skill id from a fixed list, daily count bucket |
| `usage_daily` | once a day: number of queries (bucketed), number of tool calls (bucketed), sessions (bucketed). Counts only, never the queries |
| `perf` | model id, hardware class, backend; time to first token, tokens per second, model load time, and end-to-end turn time, each as a coarse bucket (median per day), plus token-count buckets. No timing tied to any content |

Plus: random install id generated locally, day-grain timestamp, country derived at the edge.

**Never sent:** prompts, responses, file names or paths, document names, tool arguments, model output, usernames, hostnames, emails, IPs (the Worker does not store them), stack traces with paths. No third-party analytics SDK.

**Exactly what is sent — the disclosure contract**
- One schema file is the source of truth. The installer prompt, the settings screen, `gaia privacy show`, and `docs/guides/privacy.mdx` are all generated from it, so they cannot drift.
- The list is exhaustive ("only these fields"), never "such as". Anything not in the schema cannot be sent: the client has no code path for it and the server drops unknown fields.
- Every disclosure shows a **literal example payload** next to the field table, e.g. `{"event":"error","component":"installer","class":"hash-mismatch","gaia":"0.25.2","os":"windows","day":"2026-10-26"}`.
- `gaia privacy show --pending` prints the real queued payloads before they are sent, and a local log of what was sent stays readable on disk.
- The page and the privacy doc name the one host data goes to, and state that it is not shared with third parties.

**Reference: how Ollama handles it (checked Oct 2026)**
- Its [privacy policy](https://ollama.com/privacy) says it does not collect prompts, responses or model interactions, but does collect "limited device and usage metadata (such as app version and request counts)". The wording is open-ended, and the policy gives no way to switch the metadata off.
- Its desktop updater ([`app/updater/updater.go`](https://github.com/ollama/ollama/blob/main/app/updater/updater.go)) sends a request to `ollama.com/api/update` about every hour with `os`, `arch`, `version`, a timestamp, a random nonce, a device id on macOS only, and a User-Agent with the same details. Only auto-download can be turned off, not the check.
- Lessons for GAIA: keep content out of telemetry (same as Ollama), but be exhaustive instead of "such as", give a real off switch for the whole thing, and avoid a stable device id (our id rotates every 90 days). GAIA's update check should be covered by the same disclosure and the same switch, so a user who disables telemetry sends nothing beyond fetching the update itself.

**Privacy mechanics**
- The install id rotates every 90 days and on `reset-id`, so a long-lived identifier cannot form. Retention is measured per rotation window.
- Counts are bucketed (0, 1, 2–5, 6–20, 21+) rather than exact.
- The Worker rejects any event that fails schema validation and drops unknown fields.
- The dashboard hides any breakdown cell with fewer than 10 installs (k-anonymity), so small groups can't be singled out.
- Retention: raw events kept 90 days, then only aggregates remain.

**Tests (adversarial, per project rule)**
- With telemetry off, zero outbound requests — asserted by a blocked-network test.
- Schema fuzz: attempts to send a path, prompt, or email through every field are rejected client-side and server-side.
- CI and `DO_NOT_TRACK` leave telemetry off; disabling at install sends nothing, not even a "declined" ping; a default install sends only the disclosed events; toggling in settings takes effect without a restart; consent is never re-asked; `reset-id` really changes the id.
- An error event built from a real exception contains no message text, path, or username.
- Real integration test of the ingestion endpoint's contract, not just a mocked client.
- Extend the planned `test_no_silent_telemetry` check (#890) so the only allowed telemetry destination is the one documented host.

### Phase 4b — "Send this error to the GAIA team" (user-initiated reports) — DEFERRED

A separate path from automatic telemetry: the user sends one specific error, on purpose, so people can share problems they hit.

**Flow**
- When an error occurs, the Agent UI error card and the TUI show a **Send error report** action. The CLI offers `gaia report-error` (and prints the hint after a failure).
- A popup or dialog shows **the exact report, field by field, as it will be sent** — not a summary — with a free-text **Add notes** box. Nothing leaves the machine until the user presses Send; Cancel sends nothing.
- It works even when automatic telemetry is disabled, because each report is a deliberate, one-time send. Turning telemetry off never blocks a user from reporting a problem, and never sends one on their behalf.
- After sending, the user gets a **report id** they can quote in a GitHub issue or use to ask for deletion.

**What the report contains (all visible in the popup)**
- Error component and class, the error message, and a stack trace with file paths, usernames and hostnames redacted.
- GAIA version, OS, architecture, hardware class, Lemonade version and the model id in use.
- The user's notes (optional).
- Optional, off by default, each a separate checkbox with its content shown in full: the recent log tail (redacted), and the `gaia diagnostics` bundle summary. An optional contact email, only if the user types one so we can follow up.

**Never included:** prompts, responses, document contents or names, tool arguments, or memory contents. If the error message itself would contain any of these, the redactor replaces them and the popup shows the redacted text, so the user sees what we see.

**Storage and handling (the "private db")**
- A rate-limited, size-capped Worker endpoint writes to a private D1/R2 store reachable only by the team through Cloudflare Access; it is not public and not exposed through the public telemetry API.
- Redaction runs on the client (what the popup shows) and again on the server. The server rejects reports that still match path, email, key or token patterns.
- Retention 90 days unless linked to a GitHub issue; a report can be deleted on request by report id. Free-text notes are the one field a user controls, so the popup warns not to paste secrets.
- Same host and same "not shared with third parties" statement as the telemetry disclosure.

**Admin console:** an Errors panel groups reports by a fingerprint (component + error class + top frame), shows count per version and trend, and links a group to a GitHub issue. Read access is limited to the team.

**Tests (adversarial)**
- The payload the popup renders is byte-for-byte what the client sends (single serialization path).
- Planted secrets in the message, stack, notes and logs: paths, usernames, emails, API keys and tokens are redacted client-side and rejected server-side.
- Cancel and closing the popup send nothing; no send happens without the explicit button, including when telemetry is off.
- Oversized and flooded submissions are rejected by the endpoint.
- Real integration test against the ingestion endpoint contract.

### Phase 5 — Product insight panels (telemetry sample) — DEFERRED (needs Phase 4)
- Basic-tier panels (default sample, larger): active installs per 30 days from `app_start`, version and update adoption, hardware and OS mix, install success rate, and error rate by component, class and version.
- Extended-tier panels (smaller sample, shown with their sample size): the full funnel past launch, agent runs, feature use, query volume, and performance (tokens per second, time to first token, load time) per model and hardware class. Funnel steps up to launch come from Basic.
- Install funnel: downloads → install ok → first launch (Basic) → first chat → first document (Extended), with drop-off per step.
- Retention and activation: share of installs still active in the following windows; update adoption (% on latest).
- Hardware and OS mix; feature and skill usage; install failure rate per version.
- Every panel is labeled "telemetry sample, not totals" (users who disable it are not counted) with the sample size shown.

### Phase 6 — Release quality and community
- Per-release: issues filed within 7 days, regressions, patch frequency (relevant to the v0.25.x line), RC-to-final lead time.
- Community: stars/forks over time, open issues by label, time to first response, external contributors, docs-site traffic if available.
- Cost and capacity: self-hosted runner utilization, eval cost per run.

### Phase 7 — Alerts and digest
- Weekly digest to the team and threshold alerts: CI pass rate, eval score, and install success dropping sharply.
- Cross-signal view overlaying releases and CI/eval dips on adoption.

## Review findings: risks and open questions

**Blocking before telemetry ships**
- **Consent law.** A pre-selected checkbox is generally not valid consent in the EU/EEA/UK (ePrivacy/GDPR), and region detection from locale is unreliable. Options: opt-in everywhere, or opt-out only where counsel confirms it is allowed. Needs AMD privacy/legal sign-off, not an engineering call.
- **Enterprise installs.** Silent installs defaulting to on will trip IT policy. Needs an installer property (`TELEMETRY=0`) and a machine-wide policy key that overrides user settings.
- **No consent or privacy infrastructure exists yet:** no `docs/guides/privacy.mdx`, no `GAIA_TELEMETRY`/`DO_NOT_TRACK` handling in `src/` or `tui/`, and no settings surface for it. The installer prompt, settings and docs are all new work.

**Design risks**
- **"Unique installers" will be overcounted** with a 90-day rotating id (one install becomes about four ids a year) and with reinstalls. Define the KPI as *active installs per 30 days* and say so on the page; retention is only measured inside a rotation window.
- **Download deltas mix installs and updates.** If the in-app updater fetches release assets from GitHub, those count as downloads. Deleted assets and deleted RCs lower cumulative counts, so a negative delta must be handled, not clipped.
- **Eval trends are sparse.** Flagship evals run on release and RC tags, not nightly, so eval panels are per-release series. Do not draw them as a daily line.
- **Error messages are the riskiest field in error reports.** Exceptions can echo user text (for example a server error quoting a request). Regex redaction is not enough alone; the popup should let the user untick each field, and the message defaults to the sanitized error class unless they include the raw text.
- **Telemetry must fail quietly but not invisibly.** Project rules ban silent fallbacks, so a send failure is logged at debug with context and counted locally, never swallowed with a bare `except: pass`; this one deliberate exception to "fail loudly" should be written down.
- **IP handling claim.** "We do not store IPs" must be true of Cloudflare logs and any log drain too, not just our Worker code. Verify before publishing the sentence.
- **Triage ownership.** A private reports store nobody reads is worse than none. Name an owner and a weekly review, and decide whether a report auto-creates a private issue.

**Missing from the plan**
- **Success criteria.** List the five questions the page must answer and the numbers that would change a decision, for example install success rate and active installs, so the page isn't built and then ignored.
- **Secrets and access.** A GitHub token with Actions read for the collector, Access policy membership, and who administers it.
- **Schema versioning** and a client-side offline queue with bounded retries (no unbounded hidden retry loops).
- **Sizing and owners.** Rough effort per phase and a named owner for each.

**Suggested reorder**
1. Phases 1–3 now: public data only, no legal dependency.
2. Phase 4b (user-initiated reports) before 4: it is consent-per-send, so it is not blocked by the default-on question, and it gives the most direct bug signal.
3. Phase 4 only after the legal review, signed-off regional policy table, DPIA and updated privacy messaging.
4. Split the work into one tracking issue plus one issue per phase; #1821 is too large to close as a unit.

## Trust commitments (default-on telemetry)

Being on by default means trust has to be earned by what we do, not asked for. These are requirements, each backed by a test or a review rule.

1. **Say it first, say it exactly.** The disclosure contract above: exhaustive list, literal example payload, one named host, no third parties.
2. **Verifiable, not promised.** The client and schema are open source; `gaia privacy show --pending` and a local send log show real payloads; the docs explain how to check with a network proxy; a CI test fails if the client can send a field outside the schema.
3. **Off means off.** One switch in the installer and in settings, same prominence as the on state, no dark patterns, no nagging. Off sends nothing — not even a "declined" ping — works offline, and survives upgrades.
4. **Scope never widens silently.** A new field or event is a schema version bump. It ships off for existing users until they have seen a notice that lists exactly what changed; the change appears in the release notes.
5. **Hard "never" list, enforced.** No prompts, responses, file or document names, paths, tool arguments, memory, hostnames, usernames or IPs. Tests plant such values and fail if any reaches the wire.
6. **Minimum collection.** Bucketed counts, a 90-day rotating id, day-grain timestamps, small groups hidden, raw events deleted after 90 days.
7. **Nothing degrades when off.** No feature is gated on telemetry, and fully offline use is unchanged.
8. **Respect other people's controls.** `DO_NOT_TRACK`, `GAIA_TELEMETRY=0`, and an enterprise policy key always win over the default.
9. **Governance.** The schema and privacy doc need maintainer review for any change. We commit, publicly, to no sale or sharing, no ad use, no model training on it, and no per-person analysis. Admin access is limited to a named team and logged.
10. **Give something back.** Publish a public stats page (installs, error rates, what was fixed) and credit error reports in release notes ("fixed X, reported via error reports"), so users see what the data is for.
11. **Be honest about the change.** This reverses earlier "zero telemetry" positioning (#890). Say so plainly in the release notes and the privacy page instead of letting users discover it.

**Decided: two tiers.** Basic is on by default; Extended stays off until the user turns it on. Turning Basic off also turns Extended off.

## Consent and EU/EEA/UK compliance

Engineering cannot certify compliance; AMD's privacy office and counsel decide the rules, and this section makes the build able to follow whatever they decide. The rules below are our working understanding and must be confirmed by them.

**Why default-on is the risk**
- ePrivacy Art. 5(3) (and UK PECR) covers reading or storing information on a user's device, and the EDPB treats installed apps the same as websites. Analytics is generally not "strictly necessary", and legitimate interest is not an available basis for it, so consent is required.
- GDPR consent must be freely given, specific, informed and an unambiguous affirmative act. The CJEU (*Planet49*, 2019) held pre-ticked boxes invalid, so a pre-selected Basic tier does not meet the standard in these regions.
- The install id is a persistent identifier, so we should treat it as personal data even though we never see a name.
- UK rules on low-risk analytics were relaxed in 2025; counsel should confirm what applies to us there.

**Build so the legal answer can change without a rewrite**
- A **regional policy table** in one config file: region → how each tier behaves (`opt_out`, `opt_in`, `off`). Counsel owns the values. Until they confirm otherwise, EEA, UK and Switzerland are `opt_in` (unticked, nothing sent until the user says yes) for both tiers.
- **Region is decided conservatively.** The installer and the backend use system region and locale; an unknown or ambiguous region gets the strict `opt_in` behavior, never the permissive one. Whether to use IP geolocation for this must itself pass the privacy review.
- A **local consent record**: tier, notice version, timestamp, how it was given (installer / settings / CLI), and which regional policy applied. It is shown in `gaia privacy show`.
- A **notice version** is sent with each batch, so we can show which wording a given event was collected under; a wording change forces a fresh prompt.
- **Granular and symmetric:** each tier is its own choice, Accept and Decline have equal prominence, declining does not block installation, and withdrawing is as easy as consenting (one switch in settings; effective immediately, queued events discarded).
- **No bundling:** consent is not a condition of installing or using GAIA, and the prompt is separate from the license and setup steps.
- **Strictly necessary traffic stays separate:** fetching an update or a model download is the service the user asked for and is not telemetry, but it must not carry the telemetry id or extra fields.

**Data handling**
- Store EU/EEA/UK-origin data in an EU jurisdiction (Cloudflare D1 and R2 support jurisdiction restrictions); document any transfer outside it and its legal mechanism.
- Cloudflare is a processor: a data processing agreement must be in place; list it in the privacy notice.
- Access and erasure: we cannot look a person up by name, so the user can read the id with `gaia privacy show`, reset it, and request deletion of everything stored under it; document this on the privacy page.
- Records AMD's privacy office will need from us: a DPIA for the telemetry and the error-report store, an entry in the record of processing, the retention schedule (90 days raw), and a named contact.

**Compliance tests (run in CI)**
- For every region in the policy table, a fresh install in `opt_in` regions makes zero telemetry requests until consent is given.
- The installer and settings render no pre-ticked control where the policy says `opt_in`.
- Withdrawing consent stops sends and clears the queue within one run.
- An unknown or malformed region falls back to the strict behavior.
- A wording change (notice version bump) re-prompts.
- The consent record is written for every grant and withdrawal.

**Process**
1. Open the privacy review with AMD privacy/legal now, with the schema and this section as the brief, before Phase 4 starts.
2. Write the DPIA and privacy notice alongside the schema, not after.
3. Phase 4 ships only when the policy table values are signed off.

## Cross-cutting
- **Docs:** `docs/guides/privacy.mdx` (the exact list of what is sent, how to inspect and revoke), CLI reference for `gaia privacy`, the installer docs, and a `docs/docs.json` entry. The installer prompt, the settings screen, and the doc must all show the same list, enforced by a test that renders all three from the one schema.
- **Security:** the telemetry endpoint is public and unauthenticated by nature — rate-limit it, cap payload size, and treat all input as hostile.
- **Release gate:** telemetry ships only after a privacy review by the maintainer; `gaia privacy show` must match the schema doc exactly.
- **Evals:** none of this touches prompts or tools, so no agent eval is required.

## Order and dependencies
Phases 1–3 need no client change and can start now. Phase 4 gates 5; Phase 4b shares the client, redactor and endpoint work with 4 and can ship with it, and needs decision 6 (legal review, updated privacy messaging) done first. Phase 2 gates the visible parts of 3, 5, 6 and 7.
