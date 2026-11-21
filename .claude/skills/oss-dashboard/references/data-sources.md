# Data sources: limits and quirks

Write down each source's retention before designing storage — it decides what must be snapshotted nightly.

| Source | Endpoint | Keeps | Quirk that bit someone |
|--------|----------|-------|------------------------|
| Release downloads | `GET /repos/{r}/releases` `assets[].download_count` | Running total only | No per-day data, no uniques. Daily = difference of nightly snapshots; history starts at the first snapshot. A deleted file makes a negative day (reported, not clipped). |
| Stars | `GET /repos/{r}/stargazers` with `Accept: application/vnd.github.star+json` | Full, dated | **Listed only to people with access to the repo** (404 otherwise, GraphQL returns 0). Cut off at 40,000. Replace the table wholesale each run so unstars correct themselves. |
| Forks | `GET /repos/{r}/forks?sort=oldest` | Full, dated | Deleted forks disappear; replace wholesale. |
| Issues and PRs | `GET /repos/{r}/issues?state=all&sort=updated&since=` | Full | Large repos refuse page numbers (HTTP 422 "use cursor based pagination"): follow the `Link` header's `next`, never build `page=`. `author_association` is wrong for private org members. |
| Commits | `GET /repos/{r}/commits?sha={branch}&since=` | Full | `author` is null when the email is not linked to an account; group those as "(no GitHub account)" — never store the email. `since` filters by commit date, and a merged branch keeps its older dates: re-read the full list weekly. |
| Line counts | `GET /repos/{r}/stats/code_frequency`, `/stats/contributors` | Full, weekly | Not computed past **10,000 commits** (422, or zero line counts): set `CODE_STATS = false`. Answers **202 while computing**: report "not ready", retry next run, never store empty. Weeks start Sunday. Contributors list is the top 100 only; per-person lines do not sum to the repo total. |
| Workflow runs | `GET /repos/{r}/actions/runs?created=a..b` | ~90 days | **At most 1,000 results per query**: split the window in halves until each fits; fail if a 1-minute window still overflows. Duration `updated_at - run_started_at` is inflated by later events (re-runs, approvals): treat >6 h as waiting. Dynamic workflows (CodeQL default setup, Dependabot) name each run after its PR or package — group by `path`. Merge-queue runs (`merge_group`) carry the branch `gh-readonly-queue/<branch>/pr-…`. Runs from forks name the fork in `head_repository`. |
| Job logs | `GET /repos/{r}/actions/jobs/{id}/logs` | ~90 days | 302 to signed storage: follow it **without** the token (storage rejects it). Lines start with an ISO timestamp; strip ANSI codes. |
| Artifacts | `GET /repos/{r}/actions/runs/{id}/artifacts`, `/artifacts/{id}/zip` | Repo setting, default 90 days; often 14 | Same redirect rule. A re-run keeps the run id: compare `run_attempt`. Unzip in the Worker with `DecompressionStream("deflate-raw")`. |
| npm | `api.npmjs.org/downloads/range/{start}:{end}/{pkg}` | 18 months | Reports 0 for days not computed yet, also mid-history (npm outages): drop the last two days, and store a 0 as a gap for a package that normally has downloads. Scoped names work. |
| PyPI | `pypistats.org/api/packages/{pkg}/overall?mirrors=false` | 180 days | One day late. `/system` splits by OS; null-OS traffic is mostly automation. Be polite: one call per package per night. |
| crates.io | `crates.io/api/v1/crates/{name}/downloads` | 90 days | Requires a User-Agent naming the caller. Sum `version_downloads` and `meta.extra_downloads` per date. |
| Traffic | `GET /repos/{r}/traffic/views?per=day`, `/clones?per=day`, `/popular/referrers`, `/popular/paths` | 14 days | Needs push access; a fine-grained token needs **Administration: read** (403 otherwise). Days are `views[]`/`clones[]` `{timestamp, count, uniques}`, zero days included; the latest day is revised, so upsert. `uniques` is per day: never sum it over a range. Referrers and paths are 14-day top-10 totals: store a snapshot per night. |
| Docker Hub | `hub.docker.com/v2/repositories/{ns}/{repo}/` `pull_count` | Running total | Official images are `library/{name}`. Snapshot nightly; daily = difference. GHCR exposes no public counts. |
| RubyGems | `rubygems.org/api/v1/gems/{name}.json` `downloads` | Running total | Snapshot nightly. |
| NuGet | `azuresearch-usnc.nuget.org/query?q=packageid:{id}&prerelease=true` `data[].totalDownloads` | Running total | The search host is listed in `api.nuget.org/v3/index.json`. Match `data[].id` case-insensitively; an unknown id is `totalHits: 0`, not a 404. |
| conda | `api.anaconda.org/package/{channel}/{name}` sum of `files[].ndownloads` | Running total | Several MB for a big package (numpy ~7.6 MB). The top-level `ndownloads` differs slightly from the per-file sum. |
| Packagist | `packagist.org/packages/{vendor}/{package}/stats.json` `downloads.total` | Running total | `composer.json`'s `name` is the package. Also reports monthly and daily, but the dashboard differences nightly totals like the others. |
| Homebrew | `formulae.brew.sh/api/formula/{name}.json`, `/api/cask/{name}.json` `analytics.install["30d"]` | Rolling 30/90/365 days | The `30d` value is an object keyed by install option (`fzf`, `fzf --HEAD`): sum it. Already rolling: store as read, chart as "installs in the 30 days before", never difference it. |

## Token

A fine-grained token, read-only, scoped to the one repository: Contents, Metadata, Issues, Pull requests,
Actions, and Administration (read) for traffic. It must belong to a maintainer: GitHub shows stargazers to people
with access and traffic to people with push access. 5,000 requests an hour is plenty for a nightly run of
a mid-size repo; a first `full=1` on a 40k-issue repo uses ~400 for issues alone.

## Cloudflare limits that shape the design

- Workers **paid plan**: 10,000 subrequests per invocation by default, cron runs up to 15 minutes wall clock.
  Free plan: 50 subrequests — not enough. A run needs roughly: releases 1-5, issues/commits a few pages,
  stars/forks (count/100) pages, runs 3-30, tests 1 + jobs per day, evals 2-3 per run.
- D1: ~100 bound parameters per statement — never build `IN (?, ?, ...)` from a list; join or bind one JSON array
  and expand it with `json_each`. Size those arrays in **bytes**, under 1 MB. A `db.batch` is one transaction.
- D1 Time Travel restores 30 days only; export regularly, because this data exists nowhere else.

Not available anywhere: Maven Central and Go modules publish no download counts; GHCR exposes no public pull counts.

## Other sources worth adding (not in the template)

- **Website traffic / install-script hits**: Cloudflare GraphQL Analytics (`httpRequestsAdaptiveGroups`) keeps
  days to weeks — the cron must persist it.
- **PyPI by OS / completed installs**: BigQuery `bigquery-public-data.pypi.file_downloads` (needs a GCP project).
