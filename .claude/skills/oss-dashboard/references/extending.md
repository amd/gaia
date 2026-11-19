# Extending the dashboard

## Adding a source

Every source follows the same six steps; skipping one is how a number goes quietly wrong.

1. **Schema** — a new numbered file in `migrations/`. Keep raw, dated rows (one per item or per day); derive
   counts on read. If the upstream only gives a running total, store nightly snapshots and compute deltas.
2. **Collector** — a function `(env, fetcher, ...) => Promise<string>` returning a one-line summary for the
   footer. Throw with an actionable message on any failure; never return partial data as success. Write each
   all-or-nothing unit with `writeAtomic(db, insertMany(...))`. Add its name to `SOURCES` and a step in
   `collectAll` (`<dir>/src/collector.ts`), gated on config if the project may not have it.
3. **Incremental reads** — use `sinceLastSuccess(db, source)` so missed nights are caught up, and support
   `full=1` for the first fill.
4. **API** — a branch in `handleApi` (`<dir>/src/api.ts`). End windows at `collectedAt(<your source>)`, return the
   window you counted, and return `null` (not 0) for days with no data.
5. **Page** — a `card(...)` with an info text stating the definition, the bands and their source; an empty state
   that says why it is empty; respect `S.range`.
6. **Tests** — the collector against `memoryD1()` with a fake fetcher (including a failure and a re-run), the
   aggregation with fixed clocks (`vi.useFakeTimers({ toFake: ["Date"] })`), and an `audit_numbers.py` check if
   GitHub can re-count it.

Then run a full audit round (`accuracy-checklist.md`).

## Worked example: time to first human response

The one widely benchmarked community metric the template does not collect (peers: 1-48 h median on issues;
LinearB "pickup time" for PRs: <4 h good, >16 h slow).

- **Data:** for each issue and PR created since the last run, the first comment or review by someone who is
  neither the author nor a bot. REST costs one call per item (`/issues/{n}/comments?per_page=10`, plus
  `/pulls/{n}/reviews` for PRs); GraphQL gets 50 items with their first comments per call — use it for the
  backfill.
- **Schema:** `ALTER TABLE items ADD COLUMN first_response_at TEXT; ADD COLUMN first_responder TEXT`.
- **Collector:** only items with `first_response_at IS NULL` and created in the last 30 days, newest first, capped
  per run (subrequest budget); items still unanswered stay NULL — that is "no response yet", not "fast".
- **Metric:** median and P90 of `first_response_at - created_at` for items **created** in the window; show the
  unanswered count beside it, because excluding them makes a slow project look fast.
- **Bands:** issues <24 h / 1-5 days / >5 days (CHAOSS example values; say "proposed default"); PRs <4 h /
  4-16 h / >16 h (LinearB).

## Adding a registry

Add the field to `PACKAGES` in `<dir>/src/config.ts`, a loop in `collectPackageHistory` (`<dir>/src/history.ts`) with the
registry's own "not computed yet" rule, the source to `packageDaily` / `DownloadDay` and `REGISTRIES` in
`<dir>/src/page.ts`. Running totals only (Docker Hub, conda) → snapshot table instead, like release files.

## App telemetry (deliberately out of scope)

Usage from inside the app (active installs, feature use, errors) is a separate product. Under EU/EEA/UK law
(ePrivacy, Planet49) non-essential telemetry needs prior opt-in consent; "on by default with an off switch" is
not consent there. Decide it with the project's maintainers and counsel, ship it opt-in, and keep it out of the
first dashboard. A user-initiated "send this error report" button with a preview of exactly what is sent is the
low-risk first step.
