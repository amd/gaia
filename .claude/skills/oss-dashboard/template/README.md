# Project dashboard (Cloudflare Worker)

A private dashboard for maintainers: who is getting the project, how fast it and its
community grow, how development and CI are doing, and (optionally) how evals score.
Everything comes from public APIs (GitHub, package registries) and CI artifacts, except repository traffic,
which GitHub shows only to people with push access; there is no client telemetry. A nightly job keeps what GitHub throws away (daily download
counts, Actions history past ~90 days, artifacts past their retention), so this database
becomes the only long-term record of that data.

| Page | Answers |
|------|---------|
| Users | Installs per release by OS and product (update feeds and checksums kept apart), installs over time, registry downloads |
| Growth | Rates only: star and fork growth, contributor growth, newcomers, retention, outside share of merged PRs, bus factor |
| Developers | Issue and PR flow, time to merge and close (median and P90), backlog, commits, lines, contributors, core team vs outside |
| CI | Pass rate on the default branch, per-workflow health and runtime, flaky commits, CI time, test counts |
| Evals | Pass rate per run and suite, per category and per result, regressions (only when `EVAL_WORKFLOWS` is set) |

Project settings are all in `src/config.ts`.

## Run it locally

Needs Node 22.5 or later (and Python 3.11+ for the scripts in `scripts/`).

```bash
npm install
npm test                                      # "template" suite + this project's release files, tags and config
npm run migrate:local
printf 'ADMIN_DEV_NO_AUTH=1\nGITHUB_TOKEN=%s\nMAINTAINER_LOGINS=<your-login>\n' "<read-only token>" > .dev.vars
npm run dev                                   # http://127.0.0.1:8787<basePath>
curl -X POST -H "x-admin-action: collect" "http://127.0.0.1:8787<basePath>/api/collect?days=14&full=1"
GITHUB_TOKEN="$(gh auth token)" python scripts/audit_numbers.py http://127.0.0.1:8787<basePath> --repo <owner/name>
```

- Use the same read-only fine-grained token as production (below). `gh auth token` works for a quick look but is a
  broad token stored in plain text in `.dev.vars`.
- `ADMIN_DEV_NO_AUTH=1` skips sign-in for `localhost` only, and `npm run dev` binds to 127.0.0.1 so nothing else on
  the network can reach it.
- To use another port: `npx wrangler dev --local --ip 127.0.0.1 --port 8788 --local-upstream localhost:8788`.
- `MAINTAINER_LOGINS` should list every core-team account, joined with `+` per person (`alice+alice-work,bob`): a
  partial list counts the missing maintainers as outside contributors and inflates the outside share.
- On a network that inspects TLS, start with `NODE_OPTIONS=--use-system-ca`.
- Editing a source file restarts `wrangler dev` and kills a collection in progress; `.dev.vars` is read only at start.
- `full=1` reads the whole issue, pull request and commit history. Without it, those are read from one day before
  their last successful collection, so missed nights are caught up; commits are re-read in full on Sundays.
- `days=N` (1-95) is how many days of workflow runs and test counts to read; `skip=N` starts that many days back, so a
  long backfill can run in chunks. `only=a,b` re-reads just those sources; an unknown name is refused with HTTP 400.
  The sources are `releases`, `repo`, `stars`, `forks`, `issues`, `commits`, `code-stats`, `package-history`,
  `traffic`, `channels`, `runs`, `tests` and `evals`, less the ones `src/config.ts` leaves off. Collections from
  before the rename logged `code stats` and `package history`; the footer reads those as the new names.
- The collector answers only a POST carrying `x-admin-action: collect` from the same site, so a link or form on
  another site cannot trigger it.

## Deploy and sign-in

Sign-in is Cloudflare Access with a code emailed to an allowlisted address. The Worker re-checks Access's signed
token on every request and refuses to serve when Access is not configured, so a misconfigured policy cannot expose
the data.

### What you need

- A Cloudflare account on the **Workers paid plan**: a nightly collection makes hundreds to thousands of
  subrequests, far over the free plan's 50.
- For a route on your own domain, the domain's zone on that account. Otherwise the workers.dev hostname
  (scaffolded with `--workers-dev`).
- A **fine-grained GitHub token from a maintainer**, read-only on this one repository: Contents, Metadata, Issues,
  Pull requests, Actions, and Administration (read; only for `TRAFFIC`, and the owner needs push access). For a repository owned by an organisation, choose the organisation as the token's
  resource owner; the organisation may need to approve it. Tokens expire (organisations often cap them at a year):
  put the expiry date in your calendar. An expired token turns every source red, and downloads for the days until
  it is replaced are lost for good.

### 1. Database

```bash
npx wrangler login
npx wrangler d1 create <worker-name>          # paste the printed database_id into wrangler.toml
npx wrangler d1 migrations apply DB --remote
```

### 2. Secrets

```bash
npx wrangler secret put GITHUB_TOKEN          # paste when asked; never put it in a file or a chat
npx wrangler secret put MAINTAINER_LOGINS     # e.g. alice+alice-work,bob  (+ joins one person's accounts)
```

The core team decides who counts as an outside contributor and drives the "Hide maintainers" button. GitHub's
`author_association` cannot be trusted for it: people whose organisation membership is private show as
CONTRIBUTOR. `MAINTAINER_LOGINS` is the starting value; once someone saves a team from the dashboard (Developers →
Core team…), the saved list wins, with who changed it and when.

### 3. Access

The Worker must exist first, and it refuses every request until Access is set up, so deploy it now:
`npm run deploy`. Then, in the Cloudflare dashboard:

1. Zero Trust → Settings → Authentication → Login methods → add **One-time PIN** (codes by email).
2. Workers & Pages → the Worker → **Access** → Protect this Worker behind Access → **All traffic**. Policy: Allow,
   Include → Emails (or Emails ending in `@your-org`). This covers every hostname of the Worker — its route,
   workers.dev and previews — so no path can be missed.
3. Zero Trust → Access → Service Auth → create a service token for scripts. In the Access application the previous
   step created (Zero Trust → Access → Applications), add a policy with action **Service Auth**, Include → that
   token. Copy the secret now; it is shown once.
4. Copy the application's **AUD tag** into `ACCESS_AUD` in `wrangler.toml`, and your team domain
   (`<team>.cloudflareaccess.com`, under Zero Trust → Settings) into `ACCESS_TEAM_DOMAIN`. Neither is a secret.

The Worker accepts a request only when Worker-level Access passed it (checked against `ACCESS_AUD`) or when it
carries a valid Access assertion for this application (checked against the team's signing keys). If your account
lacks Worker-level Access, an Access application on the hostname works too: add both `<path>` and `<path>/*`.

### 4. Deploy and verify

```bash
npm run deploy                                # again, now with ACCESS_AUD set
export CF_ACCESS_CLIENT_ID=<client id> CF_ACCESS_CLIENT_SECRET=<client secret>   # from step 3.4
python scripts/verify_deploy.py https://<host><path>
```

Every check must pass: anonymous requests are sent to the Access sign-in, a signed-in caller gets data, the
collector refuses calls without its header or from another site. In a private window the page must ask for an
email first, and an address not on the list must be refused.

### 5. Fill the history, the same day

Issues, commits, stars and forks come back in full on the first night. Workflow runs and test counts go back
about 90 days on GitHub and are read 3 days a night, so backfill them once, in chunks:

```bash
URL=https://<host><path>
for skip in 0 30 60; do
  curl -X POST -H "x-admin-action: collect" -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" "$URL/api/collect?days=30&skip=$skip&only=runs,tests"
done
GITHUB_TOKEN="$(gh auth token)" python scripts/audit_numbers.py "$URL" --repo <owner/name>
```

On a busy repository (the probe warns past 1,000 runs a day) read one day per call instead: `days=1&skip=N` for N
from 0 to 89, one after another.

Eval artifacts expire (14 days by default) and each collection reads at most 40 runs, so repeat
`?only=evals` until the footer no longer says "evals still catching up".

## Day to day

- **Add or remove a person:** edit the Access policy. No deploy.
- **A source failing:** the footer turns red and names it; the error says what to fix.
- **Schema change:** add a numbered file under `migrations/`; `npm run deploy` applies it before deploying.
- **Backups:** D1 Time Travel restores any minute of the last 30 days. Much of this data exists nowhere else, so
  also export it regularly: `npx wrangler d1 export DB --remote --output backup-$(date +%F).sql`.
- **Where this code lives:** a private repository is simplest. In the project's public repository, review
  `references/publishing.md` of the oss-dashboard skill first. Template fixes are picked up by scaffolding a fresh
  copy and diffing it against this one; `src/config.ts` and `test/fixtures/` are yours.

## If something is wrong

| Symptom | Cause |
|---|---|
| 500 "ACCESS_AUD is not set" / "ACCESS_TEAM_DOMAIN and ACCESS_AUD are not set" | Values missing from `wrangler.toml`, or not redeployed |
| 401 "Access let this request through for a different application" | `ACCESS_AUD` is not this Worker's Access application |
| 401 "Access token is for a different application" | `ACCESS_AUD` is from another application |
| 401 "Access token issuer mismatch" | `ACCESS_TEAM_DOMAIN` is not exactly `<team>.cloudflareaccess.com` |
| verify_deploy: "Access does not cover <path>" | Access is not on for all traffic (Worker → Access), or a hostname application lacks `<path>/*` |
| 403 "send the header 'x-admin-action: collect'" | The collect call is missing that header |
| stars: HTTP 404, "token from one of its maintainers" | `GITHUB_TOKEN` belongs to someone without access to the repository |
| HTTP 401/403/429 from GitHub on every source | Token missing, expired or not approved by the organisation, lacking Actions read, or the hourly limit |
| "GitHub is still computing … statistics" | Normal on the first run; GitHub answers on a later run |
| "GET … failed: Network connection lost" | A connection that dropped on every retry (`NETWORK_RETRIES` in `src/config.ts`, 2 by default); re-run that source (`?only=runs`) — the next night also catches up |
| "GitHub lists at most 40,000 stargazers" | Set `STAR_HISTORY = false` in `src/config.ts` |
| "Hit the N-page guard" | A very large history: raise the guard deliberately (`src/history.ts` for lists, `src/collector.ts` for releases) |
| CI page cards empty | CI never runs on the default branch after merge (only on pull requests): the default-branch cards stay empty by design; "All branches and triggers" still shows every run. A merge queue is already counted |
| Tests source fails: "no log … carries a summary" | `TESTS.framework` is wrong, or Go runs without `-v`/`-json`/gotestsum (plain `go test` lists only failures) |

## Things to know

- GitHub keeps repository **traffic** for 14 days only, so its history starts 14 days before the first collection
  and a gap longer than 14 days is lost. Unique visitors are per day and cannot be added up: sums over a range are visits.
- **Other install channels** (`CHANNELS`): Docker Hub, RubyGems, NuGet, conda and Packagist report running totals (daily =
  difference of nightly readings); Homebrew reports installs over the last 30 days, charted as that, never per day.
- GitHub reports only **running totals** per release file. Daily downloads are differences between nightly
  snapshots, so that history starts at the first snapshot; a missed night is a gap, never a zero.
- Update feeds (`latest*.yml`), `.blockmap`, checksums, signatures and SBOMs are counted apart from installs. An
  app whose updater polls GitHub downloads its feed on every start.
- A file no rule matches shows as **Unclassified**, never guessed; `test/assets.test.ts` fails until `PRODUCT_RULES`
  (what it is) and the file name or `OS_RULES` (which OS) cover it. A zip that names no OS is never assumed
  cross-platform.
  When releases start using new file names, add them to `test/fixtures/release-assets.json` (a fresh probe's
  `asset_names` has them all).
- Headline numbers count the selected window by exact timestamp, ending at the last successful collection of their
  data, not at "now" (which would read low against a full earlier window).
- "Pass rate" counts `BUILD_EVENTS` runs (push, schedule, merge queue) on the default branch, minus
  `CI_EXCLUDE_WORKFLOWS`. Runs over 6 hours almost always waited for approval or a runner; they are counted as
  waiting and left out of CI time.
- Test counts come from the test runner's summary in the job logs: the largest job's count for a matrix, or the
  sum over jobs for a sharded suite (`TESTS.jobs`).
- The Actions API returns at most 1,000 runs per query, so a busy day is split into smaller windows.
- Writes that must be all-or-nothing are single D1 batches; rows are bound as one JSON array and expanded with
  `json_each`, so thousands of rows cost one statement.

Derived from the GAIA admin dashboard (github.com/amd/gaia, `workers/admin`, MIT); see NOTICE.
