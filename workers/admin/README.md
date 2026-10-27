# gaia-admin (Cloudflare Worker)

The GAIA admin dashboard at `amd-gaia.ai/admin`, in four pages:

- **Users** (`/admin`): installs per release by OS and product, installs over time, PyPI and npm.
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
printf 'ADMIN_DEV_NO_AUTH=1\nGITHUB_TOKEN=%s\n' "$(gh auth token)" > .dev.vars
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

## Deploy (maintainer)

Needs the Workers **paid** plan: the nightly collection makes far more than the free
plan's 50 subrequests per invocation and needs more than 10 ms of CPU.

The dashboard is a Worker on the `amd-gaia.ai` zone, not part of the Railway site. Its
route `amd-gaia.ai/admin*` is more specific than `website-router`'s `amd-gaia.ai/*`, so
Cloudflare sends `/admin` here and everything else to Railway as before. Railway never
sees `/admin` traffic, so its public `*.up.railway.app` hostname is not a way around
sign-in.

1. **Database.** `npx wrangler d1 create gaia-admin`, paste the id into `wrangler.toml`,
   then `npx wrangler d1 migrations apply gaia-admin --remote`.
2. **Token.** `npx wrangler secret put GITHUB_TOKEN`: a fine-grained token with read
   access to `amd/gaia` (Contents, Actions, Issues, Pull requests, Metadata). Actions
   read is needed for job logs and eval artifacts.
3. **Sign-in (Cloudflare Access, email code, allowlist).** See below. Do this before the
   first deploy: without `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` the Worker answers 500 to
   everyone rather than serving unauthenticated.
4. **Deploy.** `npx wrangler deploy`. The first nightly run (03:17 UTC) reads the full
   issue and commit history on its own. To fill 90 days of CI history right away, create
   an Access **service token** (Zero Trust → Access → Service Auth → Create service
   token), add a second policy to the application with action **Service Auth** that
   includes that token, then run the backfill in chunks:
   ```bash
   for skip in 0 30 60; do
     curl -X POST -H "x-admin-action: collect"        -H "CF-Access-Client-Id: <id>" -H "CF-Access-Client-Secret: <secret>"        "https://amd-gaia.ai/admin/api/collect?days=30&skip=$skip&only=runs,tests"
   done
   ```
   Measure the first collection against the real database before relying on it:
   `npx wrangler dev --remote` runs the Worker against remote D1.

### Sign-in with an emailed code for allowlisted addresses

Cloudflare Zero Trust (free for up to 50 users) checks identity at the edge, before
any request reaches the Worker. The Worker then re-checks the signed token Access
adds (`Cf-Access-Jwt-Assertion`), so a misconfigured policy still cannot expose data.

1. **Turn on Zero Trust.** In the Cloudflare dashboard for the account that owns
   `amd-gaia.ai`, open **Zero Trust**. The first time, pick a team name (for example
   `amd-gaia`); your team domain becomes `amd-gaia.cloudflareaccess.com`.
2. **Enable email codes.** Zero Trust → **Settings → Authentication → Login methods →
   Add new → One-time PIN**. No identity provider is needed; Access emails a six-digit
   code to the address entered.
3. **Create the application.** Zero Trust → **Access → Applications → Add an
   application → Self-hosted**.
   - Application name: `GAIA admin`
   - Session duration: 24 hours (or shorter)
   - Application domain: `amd-gaia.ai`, path `admin` (add a second entry with path
     `admin/*` so the API is covered too)
   - Identity providers: **One-time PIN** only
4. **Allowlist the emails.** In the application's **Policies** step, add a policy:
   - Action: **Allow**
   - Include → **Emails** → list each address (or **Emails ending in** `@amd.com` to
     allow everyone at AMD)
   - Save. Anyone not on the list is refused before a code is even sent.
5. **Copy the two values into `wrangler.toml`.** On the application's **Overview** tab,
   copy the **Application Audience (AUD) Tag**. Set:
   ```toml
   ACCESS_TEAM_DOMAIN = "amd-gaia.cloudflareaccess.com"
   ACCESS_AUD = "<the AUD tag>"
   ```
   then `npx wrangler deploy`.
6. **Check it.** In a private window, open `https://amd-gaia.ai/admin`. You should see
   Cloudflare's sign-in page; enter an allowlisted email, then the code from your inbox.
   An address not on the list is refused. `curl -i https://amd-gaia.ai/admin/api/status`
   without a cookie must return a redirect to the sign-in page, never data.

To add or remove someone later, edit the policy's email list; no deploy is needed.

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
- Developer and CI headline numbers count the selected window by exact timestamp; numbers
  built from daily data (stars, package downloads) count whole calendar days. Charts span
  the whole range in whole days, weeks (Monday start, UTC) or months, and say where
  collection began when the range reaches further back.
- "Pass rate on main" counts push and nightly runs only: issue- and comment-triggered
  automation and manual dispatches on `main` are not build health.
- Writes that must be all-or-nothing (a day's release snapshot, a day of workflow runs,
  an eval run, the star, fork and line-count histories) are single D1 batches, which run as
  one transaction. Rows are packed into multi-row inserts (about ten rows per statement),
  which keeps a nightly run to a few hundred statements.
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
