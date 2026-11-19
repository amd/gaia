# Metrics, thresholds and targets

What each page measures, the green / amber / red bands the template ships with, and where each band comes
from. A band with no published benchmark says **proposed default** in its info text; recalibrate those after a
quarter of the project's own data.

## Setting targets

1. Run the dashboard on real data first (`full=1`), at 90d and 1y.
2. For each growth metric, read the project's own median over the last 3-6 months.
3. Set `good` a little above that median and `warn` around 60% of it. Write them into `TARGETS` in
   `<dir>/src/config.ts`.
4. Put famous peers in `BENCHMARKS` (with the month you looked them up) as context. Never use a peer's numbers
   as the target: in the original build, targets copied from a 250k-star project turned every card red and the
   maintainer asked for "more realistic" ones the same day.

## Users

| Card | Definition | Band |
|------|------------|------|
| Installs to date | Sum of release-file downloads classified as installs (update feeds, checksums, signatures, SBOMs excluded) | none — a count |
| Package downloads | Registry downloads in the window vs the window before, both ending on the last day the registries reported | flat or up / down 10-30% / down >30% (proposed default) |
| Latest release | Days since the last final (non-RC) release | <35 / 35-60 / >60 days (proposed default) |

Raw GitHub download totals are mostly not installs when an app's updater polls GitHub: in the original build
64% of downloads were updater feed fetches. Registry counts include CI and mirrors — read them as trend, not
users. PyPI traffic with no OS (scanners, proxies) can dwarf real installs right after a release.

## Growth (rates only)

| Card | Definition | Band | Source |
|------|------------|------|--------|
| Star growth | Stars added ÷ stars at window start, compounded to 30 days | default ≥3% / 1.5-3% / below a month | Redpoint: dev-tool startups average 7.9%/month before Series A, best ~10%; set from own median |
| Fork growth | Same for forks | default ≥3% / 1.5-3% | Forks per star 0.10-0.25 is typical (peers 0.09-0.22; Borges & Valente ~0.16) |
| Contributor growth | People (no bots) who committed or opened a PR, vs the window before | ≥ as many / ≥80% / fewer | — |
| Newcomers | People whose first commit or PR falls in the window, per 30 days | set from own median | CHAOSS "new contributors"; ~1% of stars a month is strong |
| Newcomer retention | Of first-time PR authors 90-180 days ago, share who opened another PR within 90 days | ≥25% / 15-25% / <15% | Steinmacher 2013: under 20% of newcomers stay |
| Outside share | Merged PRs by people outside the core team ÷ all merged PRs (bots out) | ≥20% / 10-20% / <10% | Fast-growing community projects range widely, roughly 15-65% |
| Bus factor | Fewest people who made half the commits in the window | ≥2 / 1 (amber) | OpenSSF Scorecard asks for contributors from ≥3 organisations; small teams sit at 1-2 |
| Momentum chart | Each period vs the mean of the up-to-four before it | above 0 is faster than recent pace | — |

Under 7 days the page shows no rates and no colours: two days of forks compound to absurd monthly rates. People
metrics (contributor growth, retention, bus factor) are coloured only from 10 people, shares and ratios only from 10
PRs or issues: a project with three contributors gets the numbers without the alarms.

## Developers

| Card | Definition | Band | Source |
|------|------------|------|--------|
| PRs merged | Merged in the window; coloured by the worse of merged ÷ opened (≥80% / 60-80% / below) and pace vs the window before (within 20% / down 20-40% / more) | see definition | proposed default; Gousios et al. (ICSE 2014) found most projects merge the majority of PRs they receive |
| Time to merge | Median and P90 of created→merged for PRs merged in the window | median <25 h and P90 <72 h / up to 72 h and 161 h / slower | LinearB 2026 (8.1M PRs: elite <25 h; 73-161 h "fair"); DORA 2024 |
| Time to close (issues) | Histogram of created→closed for issues closed in the window | none | CHAOSS defines it without numbers |
| Open issues | Open now, and the backlog's change over the window | shrinking or flat (under 1%) / grew up to 10% / more | proposed default |
| Stale PRs | Open PRs created more than 14 days ago | proposed default | Big projects carry 49-82% stale PRs, so they are no bar |

The core-vs-outside table scores outside merge time on its own: comparing it with core PRs that are mostly
self-merged produced a meaningless "10.7× slower" in the original build.

## CI

| Card | Definition | Band | Source |
|------|------------|------|--------|
| Pass rate on the default branch | Successful ÷ (successful + failed) runs of `BUILD_EVENTS` (push, schedule, merge queue) on the default branch, minus `CI_EXCLUDE_WORKFLOWS`; cancelled and skipped left out | ≥90% / 80-90% / <80% | CircleCI State of Software Delivery 2025 (observed average 82%) |
| Workflows below 90% | Workflows with ≥5 decided runs and pass rate under 90% | 0 / ≤3 / more | — |
| Flaky commits | Commits where a workflow failed and a later run of it passed, ÷ commits with a decided run; coloured from 20 commits | <2% / 2-5% / >5% | Google 2016: ~1.5% of test runs flaky |
| CI time | Wall-clock time of finished runs ≤6 h, vs the window before | proposed default | CircleCI: median run 2m43s, P95 >25 min |
| Tests | Count in the latest default-branch run of the test workflow | none failing and not shrinking / shrank / failing | — |

Issue- and comment-triggered automation and manual dispatches are not build health; neither is a workflow meant
to fail (a backlog sweep) — list those in `CI_EXCLUDE_WORKFLOWS` in `<dir>/src/config.ts`.

## Evals

Pass rate = PASS ÷ (PASS + FAIL + TIMEOUT); ERROR (harness/infra) and SKIPPED are counted apart. Compare a run
only with earlier runs of the same suite, tier and model. A fixed "80%" goal is less useful than the trend
against the project's own baseline. A narrow release-gate suite can score far higher than the full nightly sweep —
show tiers apart so releases are not signed off on the easy set.

## What real dashboards surfaced

Worth telling the user, so they know what to look for:

- The desktop **auto-updater was silently broken** — visible only because updater feed downloads stopped.
- A **bus factor of 1** that read 2 until one person's two accounts were joined.
- A median time to first human reply on issues measured in weeks, where comparable projects answer within a day or two.
- Release-candidate smoke evals scoring far above the full nightly sweep, so releases were signed off on the easy set.
- A PyPI download spike that turned out to be mostly unidentified automated traffic.
- Brag material the maintainer used: first-time contributors per month, retention, main pass rate, test growth.
