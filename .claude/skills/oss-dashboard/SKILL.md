---
name: oss-dashboard
description: Build a private maintainer dashboard for an open-source GitHub project — adoption (release and registry downloads), growth (stars, forks, newcomers, retention, outside contributions, bus factor), development velocity (PRs, issues, time to merge), CI health (pass rate, flaky commits, CI time, test counts) and optional eval/benchmark results — as a Cloudflare Worker + D1 behind Cloudflare Access, with nightly collection that keeps the history GitHub throws away. Use when asked to build, scaffold, deploy, audit or extend a project/admin/maintainer/traction/metrics/KPI dashboard for an OSS repo, or to track downloads, stars, contributors or CI trends over time.
metadata:
  gaia:
    permissions:
      - env:read
      - network:write
      - filesystem:write
---

# OSS project dashboard

Builds a five-page private dashboard (Users, Growth, Developers, CI, Evals) for one GitHub repository from a
tested template. The data comes from GitHub, package registries and CI artifacts; all of it is public except
GitHub's repository traffic (views, clones, referrers), which only people with push access can see. A nightly Worker
snapshots it into D1, because GitHub keeps only running download totals, ~90 days of Actions history and
short-lived artifacts — the database becomes the only long-term record.

The build itself is quick. **Making the numbers trustworthy is the work**: in the original build, eight review
rounds found ~30 accuracy bugs that every unit test passed — almost all time-window, denominator or identity
mistakes. Budget most of the effort for step 4.

## Settle these with the user first

Ask only what you cannot learn from the probe; recommend the default.

1. **Public data plus repository traffic, no telemetry.** Default yes. Traffic is not public: it needs a token
   with push access, and the dashboard must then stay private for that reason, not just for focus. App telemetry is a separate product with consent law
   (EU/EEA/UK opt-in) attached; a first dashboard ships without it. See `references/extending.md`.
2. **Where it is served.** A path on a domain whose DNS is on Cloudflare (`example.org/dashboard*`), or the
   free `workers.dev` hostname. Either way: Cloudflare Workers **paid plan** ($5/month; the free plan's 50
   subrequests cannot run a collection).
3. **Who signs in.** Email addresses or a domain for the Access allowlist.
4. **The core team, as people.** Every GitHub account of each maintainer (`alice+alice-work,bob`). GitHub's
   `author_association` mislabels staff with private org membership as outside contributors — in the original
   build a single staff account roughly tripled the apparent outside share.
5. **Evals or benchmarks in CI?** Only if a workflow can upload results in `references/evals-contract.md`.
6. **Where the dashboard code lives.** A private repository is simplest; in the project's public repository, walk
   `references/publishing.md` first.

Needs: Node 22.5+, Python 3.11+, the GitHub CLI or a `GITHUB_TOKEN`, and a Cloudflare account.

## Workflow

`<skill>` below is this skill's directory.

### 1. Probe the repository
```bash
GITHUB_TOKEN="$(gh auth token)" python <skill>/scripts/probe_repo.py owner/name --out probe.json
```
Read every warning. Confirm with the user: default branch, the test workflow (the probe lists candidates — it
is a guess; for Go it must run with `-v`, `-json` or gotestsum to be countable), which events CI runs on the
default branch (a repo that tests only pull requests gets no default-branch cards), registry packages (in a
monorepo, pick from `package_candidates` only what users install directly — packages that depend on each other
count one install several times) and other install channels, and anything past GitHub's limits (40k stars, 10k commits for line statistics). The probe's token must be a maintainer's: GitHub shows the stargazer list only to people with access.

### 2. Scaffold and classify
```bash
python <skill>/scripts/scaffold.py --probe probe.json --out <dir> --route "example.org/dashboard*"   # or --workers-dev
cd <dir> && npm install && npm test
```
`npm test` runs two suites. "template" checks the dashboard's logic against a pinned example config and must
pass untouched. "project" checks this repository: `assets.test.ts` fails until every release file the repo has
published is an install with a product and an OS, or metadata (update feed, checksum, signature, SBOM);
`tags.test.ts` checks `RELEASE_TAG` against its real tags — in a monorepo, scaffold picks the most common tag prefix
as the main product line; confirm that choice with the user. Fix `PRODUCT_RULES`, `OS_RULES`, `META_FILES` or `RELEASE_TAG` in
`<dir>/src/config.ts`, or list a file that truly has no OS in `ACCEPTED_NO_OS` in `test/assets.test.ts` — never weaken a
test otherwise. Rename the generated product labels with the user.

### 3. Run it on real data
Follow "Run it locally" in the scaffolded `README.md`, collect with `full=1`, and open every page at every range
(24h, 7d, 30d, 90d, 1y, All). Take screenshots. An empty chart must say why it is empty.

Now set `TARGETS` from the project's own history (`references/metrics.md`): read the medians at 90d and 1y and
set each green floor a little above them. Peers go in `BENCHMARKS` as context, never as targets.

### 4. Audit until a round finds nothing
```bash
GITHUB_TOKEN="$(gh auth token)" python scripts/audit_numbers.py http://127.0.0.1:8787/<path> --repo owner/name    # scaffold copies the scripts in
```
It re-counts PRs, issues, CI pass rate, stars and downloads straight from GitHub over the exact windows the
dashboard used. Then walk `references/accuracy-checklist.md`. Best results came from independent reviewer
subagents, each given screenshots at several ranges, the checklist, and the list of what earlier rounds fixed
(so they hunt for new problems). Fix, add a test that fails without the fix, repeat. Stop only when a full round
reports no material issue. Ask the user to sanity-check headlines too — "there's no way we merged that many PRs
in 24 hours" caught a bug no test did.

### 5. Review before the code goes public
If the dashboard lives in a public repo, walk `references/publishing.md`: no personal logins or emails in code or
fixtures, no internal figures, traffic numbers or screenshots in the PR body, no unreleased plans in docs.

### 6. Deploy, verify, backfill — the same day
Follow "Deploy and sign-in" in the scaffolded `README.md`, then:
```bash
export CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=...     # the Access service token
python scripts/verify_deploy.py https://<host>/<path>
```
Every check must pass: anonymous page and API sent to the Access sign-in (a 401 from the Worker itself means
Access does not cover the path); signed-in data served; collect without its header or from another site
refused; every source ok and fresh. Backfill CI runs and evals on deploy day: run history goes back ~90 days and
artifacts only as far as the repository's retention setting (often 14 days).
The user must do three things themselves: `npx wrangler login`, create the read-only GitHub token (never
pasted into chat; `wrangler secret put` reads it), and the Zero Trust setup (no CLI).

## Rules that keep the numbers honest

- **Never invent a number.** A missed night is a gap, not a zero; a failing source shows as failing; unknown
  release files are "Unclassified", not guessed.
- **Windows end at the last successful collection of their data**, not at "now", or short ranges read low.
- **Every chart and card obeys the selected range**, and says so when it cannot (retention needs 90 days).
- **People, not logins; bots out everywhere.** Count by person with the core-team list.
- **One metric, one home.** Growth shows rates of change only; absolute counts live on Users and Developers.
- **Every threshold names its source** in the card's info text, or says "proposed default".
- **False alarms erode trust faster than missing data.** No colour on tiny samples (under 5 decided runs,
  under 7 days for rates, a near-empty previous window).
- **Every collector source fails alone and loudly** in the page footer; the others keep collecting.

## Extending

New source, metric or page: `references/extending.md` (includes time-to-first-response as a worked example).
API limits and quirks for each source: `references/data-sources.md`. UI conventions: `references/ux.md`.

## Files

| Path | What |
|------|------|
| `template/` | The Worker: `template/src/config.ts` (all project settings), collectors, API, pages, migrations, ~370 tests on real SQLite |
| `<skill>/scripts/probe_repo.py` | Sizes the repo, checks token access, finds the test workflow and the CI events on the default branch, packages, every release file and tag |
| `<skill>/scripts/scaffold.py` | Template + probe → configured project (`--route` with optional `--zone`, or `--workers-dev`): config, release-file and tag tests, and the two scripts below copied in |
| `<skill>/scripts/audit_numbers.py` | Re-counts headline numbers from GitHub over the dashboard's exact windows |
| `<skill>/scripts/verify_deploy.py` | Proves a deployment is private and its sources healthy (`--anonymous-only` before a service token exists) |
| `references/` | metrics and thresholds, data sources, accuracy checklist, UX, publishing, extending, evals contract |

The template is derived from the GAIA admin dashboard (github.com/amd/gaia `workers/admin`, MIT).
