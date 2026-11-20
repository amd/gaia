# Accuracy checklist

Every item below was a real bug in a dashboard whose unit tests were green. Walk all of them at each audit round,
at ranges 24h, 7d, 30d, 90d, 1y and All, and around UTC midnight.

## How to run a round

1. `<skill>/scripts/audit_numbers.py` — re-counts PRs, issues, CI pass rate, stars and downloads from GitHub over the
   dashboard's exact windows. Every DIFF is a bug until explained.
2. Screenshot every page at every range (headless browser or by hand). Headless Chrome/Edge can hang after writing
   the file: run each under a timeout and kill only that process (`--headless=new --virtual-time-budget=15000
   --screenshot=<file> <url>` with a 60-second limit).
3. Hand the screenshots, this checklist and **the list of what earlier rounds fixed** to 2-3 independent
   reviewer subagents with different lenses (numbers, UX, a maintainer persona asking "what needs attention?").
   Telling them what was fixed makes them hunt for new problems instead of re-reporting old ones.
4. For each finding: reproduce, fix, and add a test that **fails with the fix reverted**. A test that passes
   either way is not a test. Read the "Test Files" line of the run, not just "Tests".
5. Repeat until a full round reports no material issue.

## Time windows

- [ ] Every window ends at the **last successful collection of its own sources**, not `Date.now()`. Otherwise
      24h and 7d read low and turn red on their own.
- [ ] Charts and cards beside them use the same end. After UTC midnight a chart that uses "today" drops a day
      the card still counts.
- [ ] No axis runs into the future.
- [ ] "24h" means the same length in the current and the previous window (calendar-day vs exact-hours mismatch
      skewed CI time by ~50% at noon).
- [ ] The label matches the span summed (a "24h" card once summed 12 weeks of PRs).
- [ ] In-progress periods are marked on count charts (a partial week reads like a crash), but not on rates.
- [ ] "Updated" shows the oldest of the core sources' collection times, not the newest of any.
- [ ] Previous-window comparisons are only shown when collection fully covers the previous window.
- [ ] Tests freeze the clock the **code** reads (`vi.useFakeTimers({ toFake: ["Date"] })`), not only test dates —
      otherwise they flake around midnight UTC.

## Denominators and double counting

- [ ] Pass rates divide by decided runs (success + failure); cancelled, skipped and in-progress are out of both.
- [ ] A rate over a set of commits counts each commit once, even if several workflows flaked on it (it once
      exceeded 100%). "Flaky" requires the failure to come before the pass.
- [ ] Costs and averages divide by the same population the headline counts (skipped items out of both).
- [ ] Ratios use the right base: RCs per release divides by releases that had RCs, not all releases.
- [ ] A comparison against a near-empty base says "new" or "—", not "+8968%".
- [ ] One label, one definition. "Contributors" must not mean commit authors on one card, people without bots on
      another, and a 28-day active window on a third.
- [ ] Colours need a minimum sample: under 5 decided runs per workflow, 20 commits for flakiness, 10 people or 10
      PRs/issues for shares and ratios, 7 days for rates → no colour.
- [ ] A decline is never listed under "Going well", even inside the green band.
- [ ] A source that fails (stars without access, logs without a test summary) is explained where its numbers would be,
      not only in the footer.

## Identity

- [ ] Bots are excluded from people metrics and from PR and merge-time figures by default (`BOT_LOGINS`); showing them is
      an explicit toggle, and bot-filed issues are reported apart.
- [ ] One person's several accounts count once (core-team `+` groups) — bus factor read 2 instead of 1.
- [ ] "Outside contributor" uses the explicit core-team list, not `author_association` alone.
- [ ] Commits without a linked account are grouped, and no email address is stored.

## Source semantics

- [ ] Release downloads exclude update feeds, checksums, signatures, SBOMs; unknown files are Unclassified.
- [ ] Release candidates are detected from the tag (`-rc1`, `-beta.2`), not GitHub's prerelease flag.
- [ ] Pass rate counts `BUILD_EVENTS` runs (push, schedule, merge queue on `gh-readonly-queue/<branch>/…`) on the default
      branch only; workflows meant to fail, and housekeeping crons, are in `CI_EXCLUDE_WORKFLOWS`.
- [ ] Runs over 6 h are "waiting", out of CI time and runtimes.
- [ ] Registry days not yet computed are not stored as zero.
- [ ] A re-run (same run id, higher `run_attempt`) replaces the stored result.

## Database and client

- [ ] No `IN (?, ?, ...)` built from a list (D1 ~100 parameter limit).
- [ ] Overlapping fetches cannot overwrite newer data: each loader has its own generation counter.
- [ ] A failed or empty load does not leave a stale chart that a resize redraws.
- [ ] Selecting an item that left the list does not loop.
- [ ] Every inline page script parses (`test/pages.test.ts`) — a stray `'+` blanked a whole page once.
- [ ] Tables sort by value: dates as dates, durations with units, `3/7` by rate, "—" always last.
