# gaia-admin (Cloudflare Worker)

The GAIA admin dashboard at `amd-gaia.ai/admin`, in five pages:

- **Users** (`/admin`): installs per release by OS and product, installs over time, PyPI and npm.
- **Growth** (`/admin/growth`): how fast stars, forks, contributors and downloads grow,
  newcomers, retention, the share of merged PRs from outside the core team, and bus factor.
  Each is scored against a target set a little above GAIA's own recent median, and set
  beside Hermes Agent and other fast-growing AI-agent projects for context (`TARGETS` and
  `BENCHMARKS` in `src/growthpage.ts`).
- **Developers** (`/admin/developers`): issues, pull requests, time to merge and close (median
  and P90), commits, lines of code, contributors (with a filter to hide chosen people or
  bots), stars and forks.
- **CI** (`/admin/ci`): run outcomes, unit-test counts, where CI time goes, per-workflow health.
- **Evals** (`/admin/evals`): agent-eval pass rate per run and tier, tied to the commit, tag
  or branch measured; per-category and per-scenario results with regressions; SWE-bench.

Headline numbers are coloured green, amber or red against thresholds stated in each
card's info text, and amber or red ones are listed at the top of the page.

Plan: [`docs/plans/admin-console.md`](../../docs/plans/admin-console.md); issue:
[#1821](https://github.com/amd/gaia/issues/1821). No client telemetry: everything comes
from public APIs (GitHub Releases, Actions, issues, commits, stargazers; npm; PyPI).

## Run it locally

```bash
cd workers/admin
npm install
npm run migrate:local                      # creates the local D1 tables
printf 'ADMIN_DEV_NO_AUTH=1\nGITHUB_TOKEN=%s\nMAINTAINER_LOGINS=<your-login>\n' "$(gh auth token)" > .dev.vars
npm run dev                                # http://localhost:8787/admin
curl -X POST -H "x-admin-action: collect" "http://localhost:8787/admin/api/collect?days=14&full=1"   # first fill
```

- `ADMIN_DEV_NO_AUTH=1` skips Cloudflare Access and works for `localhost` only; the
  Worker refuses it on any other host.
- On a network that inspects TLS, start with `NODE_OPTIONS=--use-system-ca`.
- `full=1` reads the whole issue, pull request and commit history (a few minutes).
  Without it, issues and commits are read from one day before their last successful
  collection, so missed nights are caught up. Stars, forks, line statistics and package
  history are re-read in full every night; evals cover the last 14 days.
- `days=N` is how many days of workflow runs and test counts to read, newest first
  (the nightly job reads 3). GitHub keeps about 90 days; `skip=N` lets a long backfill
  run in chunks (`days=30`, then `days=30&skip=30`, ...). Busy days take about a minute each.
- `only=a,b` re-reads just those sources (names as in the data-collection log).
- The collector only answers a request carrying `x-admin-action: collect` from the same
  site, so a link or form on another site cannot trigger it.

## Deploy and sign-in (maintainer)

The dashboard is a Worker on the `amd-gaia.ai` zone, not part of the Railway site. Its
route `amd-gaia.ai/admin*` is more specific than `website-router`'s `amd-gaia.ai/*`, so
Cloudflare sends `/admin` here and everything else to Railway as before. Railway never
sees `/admin` traffic, so its `*.up.railway.app` hostname is not a way around sign-in.
Sign-in is Cloudflare Access with a code emailed to an allowlisted address; the Worker
re-checks Access's signed token (`Cf-Access-Jwt-Assertion`) on every request, so a
misconfigured policy still cannot expose data.

### What you need

- The Cloudflare account that owns `amd-gaia.ai`, with the **Workers paid plan** (the
  nightly collection exceeds the free plan's 50 subrequests and 10 ms CPU per run).
- Zero Trust enabled on that account (free for up to 50 users).
- A GitHub fine-grained token with **read** access to `amd/gaia`: Contents, Actions,
  Issues, Pull requests, Metadata. Actions read is needed for job logs and eval artifacts.
- Node 22+ and this repo checked out: `cd workers/admin && npm ci`.

### 1. Log in to Cloudflare from the CLI

```bash
npx wrangler login          # opens a browser; pick the account that owns amd-gaia.ai
npx wrangler whoami         # confirm the account
```

### 2. Create the database

```bash
npx wrangler d1 create gaia-admin
```

Copy the printed `database_id` into `wrangler.toml` (replace the all-zero placeholder),
then create the tables:

```bash
npx wrangler d1 migrations apply gaia-admin --remote
```

### 3. Store the GitHub token and the maintainers' accounts

```bash
npx wrangler secret put GITHUB_TOKEN        # paste the token when asked
npx wrangler secret put MAINTAINER_LOGINS   # comma-separated, + joins one person's accounts: alice+alice-amd,bob
```

`MAINTAINER_LOGINS` drives the Developers page's "Hide maintainers" button, so the
team's own work can be set aside to see outside contributions, and defines the core team
on the Growth page (with GitHub organisation members and collaborators). It is kept out of the
repo on purpose; leave it unset and the button does not appear, and anyone outside the
organisation counts as an outside contributor.

### 4. Set up the email-code sign-in

1. **Team.** Cloudflare dashboard → **Zero Trust**. The first time, choose a team name,
   for example `amd-gaia`. The team domain is then `amd-gaia.cloudflareaccess.com`
   (shown under **Settings → Custom pages**).
2. **Email codes.** Zero Trust → **Settings → Authentication → Login methods → Add new →
   One-time PIN**. No identity provider is needed: Access emails a six-digit code.
3. **Application.** Zero Trust → **Access → Applications → Add an application →
   Self-hosted**.
   - Name: `GAIA admin`
   - Session duration: 24 hours (or shorter)
   - Public hostname: domain `amd-gaia.ai`, path `admin`. Add a second hostname entry
     with path `admin/*` so the API under it is covered too.
   - Login methods: **One-time PIN** only; turn off "Accept all available identity
     providers".
4. **Who may sign in.** In the application's **Policies** step, add a policy:
   - Name `Admins`, action **Allow**
   - Include → **Emails** → each allowed address (or **Emails ending in** `@amd.com`
     for everyone at AMD)
   - Anyone not on the list is refused before a code is sent.
5. **Service token for scripts** (used for the backfill in step 7, and any automation):
   - Zero Trust → **Access → Service Auth → Create service token**, name `gaia-admin-backfill`.
     Copy the Client ID and Client Secret now; the secret is shown once.
   - Back in the `GAIA admin` application, add a second policy: name `Scripts`, action
     **Service Auth**, Include → **Service Token** → `gaia-admin-backfill`.
6. **AUD tag.** Open the application → **Overview** (or Basic information) and copy the
   **Application Audience (AUD) Tag**.

Put the two values in `wrangler.toml`:

```toml
ACCESS_TEAM_DOMAIN = "amd-gaia.cloudflareaccess.com"
ACCESS_AUD = "<the AUD tag>"
```

Until both are set, the Worker answers 500 to every request rather than serving the
dashboard unauthenticated. Neither value is a secret.

### 5. Deploy

```bash
npx wrangler deploy
```

This creates the `amd-gaia.ai/admin*` route and the nightly 03:17 UTC collection.

### 6. Check the sign-in

- In a private browser window, open `https://amd-gaia.ai/admin`. You should land on
  Cloudflare's sign-in page. Enter an allowlisted email, then the code from your inbox;
  the dashboard opens.
- Enter an address that is not on the list: it must be refused.
- Without a session, `curl -i https://amd-gaia.ai/admin/api/status` must return a
  redirect to the sign-in page (or 403), never data.

### 7. Fill the history

The first nightly run reads the full issue, commit, star and fork history by itself. CI
runs and test counts go back about 90 days on GitHub and are read 3 days per night, so
backfill them once with the service token (about a minute per day of runs, so the 90
days take a while; run the three chunks one after another):

```bash
ID=<client id>; SECRET=<client secret>
for skip in 0 30 60; do
  curl -X POST \
    -H "x-admin-action: collect" \
    -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" \
    "https://amd-gaia.ai/admin/api/collect?days=30&skip=$skip&only=runs,tests"
done
```

Each call returns `ok`, or the sources that failed and why. The footer of every page
shows the last result of each source.

### Day to day

- **Add or remove a person:** edit the `Admins` policy's email list. No deploy needed.
- **A source failing:** the footer turns red and lists it; the error says what to fix.
- **Backups:** D1 Time Travel restores to any minute in the last 30 days
  (`npx wrangler d1 time-travel restore gaia-admin --timestamp=<ISO time>`). For a copy
  older than that, `npx wrangler d1 export gaia-admin --remote --output backup.sql`.
- **Schema changes:** add a numbered file under `migrations/`, then
  `npx wrangler d1 migrations apply gaia-admin --remote` before `npx wrangler deploy`.

### If something is wrong

| Symptom | Cause |
|---|---|
| 500 "ACCESS_TEAM_DOMAIN and ACCESS_AUD are not set" | Step 4.6 values missing from `wrangler.toml`, or not redeployed |
| 401 "Access token is for a different application" | `ACCESS_AUD` is from another application; copy it again |
| 401 "Access token issuer mismatch" | `ACCESS_TEAM_DOMAIN` is not exactly `<team>.cloudflareaccess.com` |
| The page loads without asking to sign in | The Access application does not cover the path; check both `admin` and `admin/*` |
| 403 "send the header 'x-admin-action: collect'" | The collect call is missing that header |
| Collection fails with HTTP 403/429 from GitHub | `GITHUB_TOKEN` missing, expired, or lacking Actions read |

## Things to know

- GitHub only reports **running totals** per release file. Downloads per day come
  from consecutive nightly snapshots, so that history starts with the first
  snapshot; a missed day is a gap, never a zero. Stars, forks, issues, pull requests
  and commits carry their own dates, so their full history is available at once.
- `latest*.yml`, `.blockmap`, signatures and checksums are counted as *update
  checks*, not installs. Desktop apps from v0.17 to v0.22 fetch `latest.yml` from
  GitHub on every start; current versions check `hub.amd-gaia.ai/updates` instead.
- Files the classifier does not recognise show as **Unclassified**, not guessed. Add
  a rule in `src/classify.ts` and a case in `test/classify.test.ts`.
- Developer and CI headline numbers count the selected window by exact timestamp, ending at
  the last successful collection of their data (not at "now", which would read low against
  a full earlier window); numbers built from daily data (stars, package downloads) count
  whole calendar days through the last one fully collected. Charts span
  the whole range in whole days, weeks (Monday start, UTC) or months, and say where
  collection began when the range reaches further back.
- "Pass rate on main" counts push and nightly runs only: issue- and comment-triggered
  automation and manual dispatches on `main` are not build health.
- Writes that must be all-or-nothing (a day's release snapshot, a day of workflow runs,
  an eval run, the star, fork and line-count histories) are single D1 batches, which run as
  one transaction. Rows are bound as one JSON array per statement and expanded with
  `json_each`, so thousands of rows cost one statement and a nightly run stays at a few
  dozen statements. Tests run this SQL against real SQLite (`test/d1.ts`).
- Workflow runs that took longer than 6 hours almost always sat waiting for approval or a
  runner. They are counted separately ("waiting") and left out of CI time and runtimes.
- Cancelled eval runs are stored but left out of pass rates: they only measured the lanes
  that finished. A re-run of failed lanes keeps the run id; the collector re-reads it when
  GitHub reports a newer attempt.
- Each collection downloads at most 40 new or changed eval runs, newest first across the
  workflows; the rest wait for the next night (artifacts last 14 days, so the nightly job
  catches up well within that). With `full=1` every run is re-read, the ones read longest ago
  first, so repeating the call works through all of them.
- Unit-test counts are read from the pytest summary line in the main-branch
  `Unit Tests` job log (the workflow uploads no JUnit file). Change `TEST_WORKFLOW`
  in `wrangler.toml` to follow a different workflow.
- The Actions API returns at most 1,000 runs per query, so a busy day is split into
  smaller windows until each fits.
- Eval results come from the artifacts of `eval_flagship.yml`, `test_gaia_agent_eval.yml`
  and `eval_swebench.yml` (`EVAL_WORKFLOWS` in `src/evals.ts`). GitHub deletes those
  artifacts after 14 days, so the nightly job re-reads the last 14 days and this
  database is the only long-term record. Re-read one source with `?only=evals`.
- SWE-bench tasks whose model backend never answered are stored as `INFRA_ERROR`, not
  `FAIL`, so a broken backend does not look like a broken agent.

## Tests

```bash
npm test          # classification, deltas, momentum, CI stats, pytest parsing, Access JWT, collector
npm run typecheck
```
