# Admin console — adoption, development, CI and eval health

Plan for [#1821](https://github.com/amd/gaia/issues/1821).

> **Status:** the dashboard on public data is built in
> [`workers/admin`](../../workers/admin/README.md). Release-quality signals and alerts are
> next. App telemetry is out of scope for now.

## Goal

One private page for maintainers that answers: is GAIA being adopted, is the project
moving, is the build healthy and getting better, and how does the agent score on its
evals, with history, so we see trends rather than snapshots.

## Principles

- **Public data only.** GitHub releases, Actions, issues, pull requests, commits,
  stargazers and forks; npm and PyPI download counts; eval artifacts from CI. No client
  telemetry.
- **Keep what GitHub drops.** A nightly snapshot in D1 preserves what GitHub discards or
  never records: eval artifacts (14 days), Actions history (~90 days), and per-day
  download counts (GitHub only reports running totals).
- **Never invent a number.** A missed night is a gap, not a zero; a failing source is
  shown as failing; every headline number links to its definition.
- **Private by default.** Cloudflare Access with an emailed code for an allowlist; the
  Worker verifies Access's signed token and refuses to serve when Access is not
  configured.

## Built

| Page | Answers |
|------|---------|
| Users | Installs per release by OS and product (update-feed polling separated from installs), installs over time, PyPI and npm |
| Developers | Issue and PR flow, backlog, time to merge and close (median and P90), commits, lines of code, contributors, stars and forks |
| CI | Run outcomes, unit-test counts, where CI time goes, per-workflow health and runtime |
| Evals | Pass rate per run and tier tied to the commit or release measured, per-category and per-scenario results, regressions, SWE-bench |

Headline numbers are coloured against thresholds stated in each card (CHAOSS
responsiveness guidance, LinearB cycle-time benchmarks), and any amber or red ones are
listed at the top of the page.

## Next

1. **Release quality:** issues filed within 7 days of a release, regressions per
   release, RC-to-final lead time.
2. **Alerts and a weekly digest:** notify on a sharp drop in CI pass rate, eval score or
   install volume, so the page does not have to be watched.
3. **Backups beyond D1's 30-day restore:** a weekly `wrangler d1 export` to R2, since
   much of this history exists nowhere else once GitHub drops it.
4. **Retention, if growth ever needs it:** keep raw CI runs for 1–2 years and only daily
   totals after that. At today's volume the database grows about 450 MB a year against
   a 10 GB limit.
