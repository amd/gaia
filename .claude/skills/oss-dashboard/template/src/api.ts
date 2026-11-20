import { DEFAULT_BRANCH_BUILD_SQL, isDefaultBranchBuild } from "./build";
import { channelView, type ChannelRow } from "./channels";
import { currentSourceName } from "./collector";
import { BUILD_EVENTS, CI_EXCLUDE_WORKFLOWS, PROJECT } from "./config";
import {
  contributors,
  cumulativeDaily,
  firstCommits,
  isBot,
  keepAuthor,
  momentumDaily,
  parsePeople,
  packageDaily,
  rangeSummary,
  type CommitRow,
  type DailyRow,
  type ItemRow,
  type RangeSummary,
} from "./momentum";
import { evalView, matchesTrigger, parseFilter, type EvalResultRow, type EvalRunRow } from "./evalstats";
import { activity, isExternal, downloadPeriods, downloadWindows, growthPeriods, growthWindow, newcomerRetention, per30, RETURN_DAYS, type DownloadDay, type GrowthWindow } from "./growth";
import { ciStats, dailyDeltas, releaseDownloads, type AssetRow, type ReleaseRow, type RunRow } from "./stats";

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

export const RANGES = [1, 7, 14, 30, 60, 90, 180, 365] as const;

/** Range in days; "all" is effectively unbounded. */
export function rangeDays(raw: string | null): number {
  const v = raw ?? "30";
  if (v === "all") return 36500;
  const n = Number(v);
  if (!(RANGES as readonly number[]).includes(n)) {
    throw new RangeError(`range must be one of ${RANGES.join(", ")} or all (got "${v}").`);
  }
  return n;
}

/** core=alice+alice-work,bob as one lower-case list of logins per person. */
const coreGroups = (raw: string | null): string[][] =>
  (raw ?? "").split(",").map((g) => g.split("+").map((x) => x.trim().toLowerCase()).filter(Boolean)).filter((g) => g.length);

/** Each core-team login (lower-case) to its person's first login. */
export const coreAliases = (raw: string | null): Map<string, string> =>
  new Map(coreGroups(raw).flatMap((g) => g.map((l) => [l, g[0]!] as [string, string])));

/** Runs longer than this are treated as having waited (for approval or a runner), not as CI work. */
export const LONG_RUN_S = 6 * 3600;

/** `o` with `keys` set to null when `unknown` is set: figures from a source never collected are unknown, not zero. */
const blank = (o: object, unknown: unknown, keys: readonly string[]): Record<string, unknown> =>
  unknown ? { ...o, ...Object.fromEntries(keys.map((k) => [k, null])) } : { ...o };

// Figures counted from issues and pull requests, and from commits.
const ITEM_SUMMARY = ["prsOpened", "prsMerged", "issuesOpened", "issuesClosed", "openIssues", "openPrs", "staleOpenPrs", "medianDaysToMerge", "p90DaysToMerge", "medianDaysToClose", "p90DaysToClose"];
const ITEM_DAILY = ["issuesOpened", "issuesClosed", "openIssues", "prsOpened", "prsMerged", "prsClosedUnmerged", "openPrs"];
const COMMIT_SUMMARY = ["commits", "contributors"];
const COMMIT_DAILY = ["commits", "activeContributors28d", "newContributors", "totalContributors"];


export async function handleApi(db: D1Database, path: string, url: URL): Promise<Response> {
  let days: number;
  try {
    days = rangeDays(url.searchParams.get("range"));
  } catch (e) {
    return json({ error: (e as Error).message }, 400);
  }
  const all = async <T>(sql: string, ...args: unknown[]): Promise<T[]> =>
    (await db.prepare(sql).bind(...args).all<T>()).results;
  const today = new Date().toISOString().slice(0, 10);
  // Windows end where the data does: at the last successful collection of the sources an endpoint
  // reads, not at "now", or a window that runs past the data reads low against a full earlier one.
  const collectedAt = async (...sources: string[]): Promise<number | null> => {
    const r = await db
      .prepare(`SELECT MIN(t) AS t FROM (SELECT MAX(finished_at) AS t FROM collect_log WHERE ok = 1 AND source IN (${sources.map(() => "?").join(", ")}) GROUP BY source)`)
      .bind(...sources)
      .first<{ t: string | null }>();
    return r?.t ? Date.parse(r.t) : null;
  };
  // A source never collected has no rows, which every count would read as zero: say so instead, with its last error.
  const notCollected = async (source: string, what: string): Promise<{ empty: true; reason: string } | null> => {
    if (await db.prepare("SELECT 1 AS x FROM collect_log WHERE source = ? AND ok = 1 LIMIT 1").bind(source).first()) return null;
    const failed = await db.prepare("SELECT detail FROM collect_log WHERE source = ? ORDER BY id DESC LIMIT 1").bind(source).first<{ detail: string }>();
    return { empty: true, reason: `${what} have not been collected yet${failed ? `: ${failed.detail}` : ". Run the collector (see README)."}` };
  };
  const missingSources = async () => ({ issues: await notCollected("issues", "Issues and pull requests"), commits: await notCollected("commits", "Commits") });
  let base = Date.now();
  const sinceIso = (d: number): string => new Date(base - d * 86_400_000).toISOString();
  const sinceDay = (d: number): string => sinceIso(d).slice(0, 10);

  if (path === "/api/downloads") {
    const latest = await db.prepare("SELECT MAX(day) AS day FROM asset_snapshots").first<{ day: string | null }>();
    if (!latest?.day) {
      const ran = await db.prepare("SELECT 1 AS x FROM collect_log WHERE source = 'releases' AND ok = 1 LIMIT 1").first();
      return json({ empty: true, reason: ran ? "This repository has published no release files yet." : "No snapshots yet. Run the collector (see README)." });
    }
    const releases = await all<ReleaseRow>("SELECT * FROM releases");
    const assets = await all<AssetRow>("SELECT tag, asset, downloads FROM asset_snapshots WHERE day = ?", latest.day);
    const hist = await all<{ day: string; tag: string; asset: string; downloads: number }>(
      "SELECT day, tag, asset, downloads FROM asset_snapshots WHERE day >= ?",
      // From the start of the first whole week or month the page will show, plus the day before it.
      sinceDay(days + 32),
    );
    const firstDay = (await db.prepare("SELECT MIN(day) AS day FROM asset_snapshots").first<{ day: string }>())?.day;
    const { points, negativeDays } = dailyDeltas(hist);
    return json({
      snapshotDay: latest.day,
      firstSnapshotDay: firstDay,
      releases: releaseDownloads(releases, assets),
      daily: points,
      negativeDays,
    });
  }

  /**
   * Stars per day: the dated stargazer list when it could be read; otherwise day-to-day changes of the nightly
   * repository snapshot, whose first row carries the whole total so far, so rates start the day after it.
   */
  const starRows = async (): Promise<{ rows: DailyRow[]; from: "list" | "snapshots" | null; since: string | null }> => {
    const list = await all<DailyRow>("SELECT day, n FROM star_daily ORDER BY day");
    if (list.length) return { rows: list, from: "list", since: list[0]!.day };
    const snaps = await all<{ day: string; stars: number }>("SELECT day, stars FROM repo_snapshots ORDER BY day");
    if (!snaps.length) return { rows: [], from: null, since: null };
    return { rows: snaps.map((s, i) => ({ day: s.day, n: i ? s.stars - snaps[i - 1]!.stars : s.stars })), from: "snapshots", since: snaps[0]!.day };
  };

  if (path === "/api/traction") {
    const starHistory = await starRows();
    const stars = starHistory.rows;
    const forks = await all<DailyRow>("SELECT day, n FROM fork_daily ORDER BY day");
    const pkgs = await all<{ source: string; day: string; downloads: number }>(
      "SELECT source, day, SUM(downloads) AS downloads FROM package_daily GROUP BY source, day ORDER BY day",
    );
    const repo = await db.prepare("SELECT stars, forks FROM repo_snapshots ORDER BY day DESC LIMIT 1").first<{ stars: number; forks: number }>();
    return json({
      starHistory: { from: starHistory.from, since: starHistory.since },
      starsHeadline: repo?.stars ?? null,
      forksHeadline: repo?.forks ?? null,
      // From snapshots, the first row carries every star so far: it is a starting total, not stars added that day.
      stars: cumulativeDaily(stars, today).map((r, i) => (i === 0 && starHistory.from === "snapshots" ? { ...r, added: null } : r)),
      forks: cumulativeDaily(forks, today),
      packages: packageDaily(pkgs, today),
    });
  }

  if (path === "/api/growth") {
    // core=alice+alice-work,bob: one entry per person, + joining one person's accounts.
    const groups = coreGroups(url.searchParams.get("core"));
    const maintainers = new Set(groups.flat());
    const alias = coreAliases(url.searchParams.get("core"));
    const starHistory = await starRows();
    const stars = starHistory.rows;
    const forks = await all<DailyRow>("SELECT day, n FROM fork_daily ORDER BY day");
    const items = await all<ItemRow>("SELECT kind, created_at, closed_at, merged_at, assoc, author FROM items WHERE kind = 'pr'");
    const commits = await all<CommitRow>("SELECT day, at, author FROM commits");
    const shift = (day: string, n: number) => new Date(Date.parse(day) + n * 86_400_000).toISOString().slice(0, 10);
    // Whole days through the last one fully collected: a collection on day D covers the days before it.
    // Stars are left out: a failing star source must not hold every other growth number back.
    const asOf = await collectedAt("issues", "commits");
    const end = asOf ? new Date(asOf).toISOString().slice(0, 10) : shift(today, 1);
    const allTime = days >= 36500;
    const first = [stars[0]?.day, forks[0]?.day, commits.map((c) => c.day).sort()[0]].filter(Boolean).sort()[0] ?? today;
    // Whole calendar days ending today, as the page's charts count them.
    const span = allTime ? Math.round((Date.parse(end) - Date.parse(first)) / 86_400_000) : Math.max(2, days);
    const from = allTime ? first : new Date(Date.parse(end) - span * 86_400_000).toISOString().slice(0, 10);
    const prevFrom = new Date(Date.parse(from) - span * 86_400_000).toISOString().slice(0, 10);
    const current = growthWindow(stars, forks, items, commits, maintainers, from, end, alias);
    // These are complete dated lists from GitHub, so the window before is always known (zero before the project began).
    const previous = allTime ? null : growthWindow(stars, forks, items, commits, maintainers, prevFrom, from, alias);
    // Snapshot-based stars are unknown before the first snapshot: a window reaching back to it has no star rate.
    const starsUnknown = (since: string) => !starHistory.from || (starHistory.from === "snapshots" && since <= starHistory.since!);
    if (starsUnknown(from)) current.starGrowth = null;
    if (previous && starsUnknown(prevFrom)) previous.starGrowth = null;
    const repoNow = await db.prepare("SELECT forks FROM repo_snapshots ORDER BY day DESC LIMIT 1").first<{ forks: number }>();
    const gran = !allTime && days <= 90 ? "week" : "month";
    // Periods start four before the range, so the page can compare the first ones with a recent pace.
    const baseFrom = shift(from, gran === "week" ? -28 : -124);
    // Daily downloads: GitHub installs from consecutive snapshots, registries as reported.
    const hist = await all<{ day: string; tag: string; asset: string; downloads: number }>(
      "SELECT day, tag, asset, downloads FROM asset_snapshots WHERE day >= ?",
      // A week of slack: registries report late, so their windows end, and start, a few days early.
      allTime ? "0000" : shift(prevFrom < baseFrom ? prevFrom : baseFrom, -8),
    );
    const pkgs = await all<{ source: string; day: string; downloads: number }>("SELECT source, day, SUM(downloads) AS downloads FROM package_daily WHERE day >= ? GROUP BY source, day", allTime ? "0000" : shift(prevFrom < baseFrom ? prevFrom : baseFrom, -7));
    const firsts = Object.fromEntries(
      (await all<{ source: string; day: string }>("SELECT source, MIN(day) AS day FROM package_daily GROUP BY source UNION ALL SELECT 'installs', MIN(day) FROM asset_snapshots")).map((r) => [r.source, r.day]),
    );
    // Installs are differences between snapshots, so the first one counts from the day after.
    if (firsts.installs) firsts.installs = shift(firsts.installs, 1);
    const firstRegistryDay = [firsts.pypi, firsts.npm, firsts.crates].filter((d): d is string => !!d).sort()[0];
    if (firstRegistryDay) firsts.packages = firstRegistryDay;
    const dl = new Map<string, DownloadDay>();
    const at = (day: string) => dl.get(day) ?? dl.set(day, { day, installs: null, pypi: null, npm: null, crates: null, packages: null }).get(day)!;
    for (const p of dailyDeltas(hist).points) at(p.day).installs = p.install;
    for (const r of pkgs) if (r.source === "pypi" || r.source === "npm" || r.source === "crates") {
      const d = at(r.day);
      d[r.source] = (d[r.source] ?? 0) + r.downloads;
      d.packages = (d.packages ?? 0) + r.downloads;
    }
    const dlDays = [...dl.values()].sort((a, b) => a.day.localeCompare(b.day));
    // Contributors are counted from commits and pull requests together, so either one missing leaves them unknown.
    const missing = await missingSources();
    const people = missing.issues ?? missing.commits;
    const known = (o: object, items: string[], both: string[], byCommits: string[]) =>
      blank(blank(blank(o, missing.issues, items), people, both), missing.commits, byCommits);
    const win = (w: GrowthWindow) => ({
      ...known(w, ["prsMerged", "externalShare"], ["active", "newContributors"], ["busFactor"]),
      starRate30: per30(w.starGrowth, span),
      forkRate30: per30(w.forkGrowth, span),
    });
    return json({
      from,
      days: span,
      gran,
      notCollected: missing,
      starHistory: { from: starHistory.from, since: starHistory.since },
      // GitHub's fork list can hold far more forks than its own count (forks of forks, deleted parents).
      forkCheck: { listed: forks.reduce((a, r) => a + r.n, 0), github: repoNow?.forks ?? null },
      current: win(current),
      previous: previous && win(previous),
      // People whose first PR was 90 to 180 days ago, so every one has had the full 90 days.
      newcomers: missing.issues ?? newcomerRetention(activity(items, [], alias), shift(today, -2 * RETURN_DAYS), shift(today, -RETURN_DAYS), today),
      periods: growthPeriods(stars, forks, items, commits, maintainers, gran, today, allTime ? from : baseFrom, alias).map((p) =>
        known(p, ["newcomerCohort", "newcomerReturned", "prsMerged", "externalMerged"], ["active", "newContributors"], []),
      ),
      downloads: allTime ? null : downloadWindows(dlDays, span, end, firsts),
      downloadPeriods: downloadPeriods(dlDays, gran, allTime ? from : baseFrom, today),
    });
  }

  if (path === "/api/momentum") {
    const people = parsePeople(url.searchParams);
    const keep = keepAuthor(people);
    const allItems = await all<ItemRow>("SELECT kind, created_at, closed_at, merged_at, assoc, author FROM items");
    const allCommits = await all<CommitRow>("SELECT day, at, author FROM commits");
    const weekly = await all<{ author: string; week: string; commits: number; additions: number; deletions: number }>(
      "SELECT author, week, commits, additions, deletions FROM contributor_weekly",
    );
    const items = allItems.filter((i) => keep(i.author));
    const commits = allCommits.filter((c) => keep(c.author));
    base = (await collectedAt("issues", "commits")) ?? base;
    const until = new Date(base).toISOString();
    const since = sinceIso(days);
    const prevSince = new Date(Date.parse(since) - days * 86_400_000).toISOString();
    // core=alice+alice-work,bob: + joins one person's accounts, so "Who contributed" lists the person once.
    const alias = coreAliases(url.searchParams.get("core"));
    const firstCommitBy = firstCommits(allCommits, alias);
    // Lines per week: GitHub's repository total, or the sum over the people kept when anyone is filtered out.
    const filtered = people.exclude.size > 0 || !people.bots;
    const loc = filtered
      ? [...weekly.filter((w) => keep(w.author)).reduce((m, w) => {
          const v = m.get(w.week) ?? { week: w.week, additions: 0, deletions: 0 };
          v.additions += w.additions;
          v.deletions += w.deletions;
          return m.set(w.week, v);
        }, new Map<string, { week: string; additions: number; deletions: number }>()).values()].sort((x, y) => x.week.localeCompare(y.week))
      : await all<{ week: string; additions: number; deletions: number }>("SELECT week, additions, deletions FROM loc_weekly ORDER BY week");
    // Core team against outside contributors, people only and whatever the people filter hides.
    const maintainers = new Set((url.searchParams.get("core") ?? "").split(/[,+]/).map((x) => x.trim().toLowerCase()).filter(Boolean));
    const humans = allItems.filter((i) => i.author && !isBot(i.author));
    const outside = humans.filter((i) => isExternal(i, maintainers));
    const core = humans.filter((i) => !isExternal(i, maintainers));
    const brief = (s: RangeSummary) => ({ prsOpened: s.prsOpened, prsMerged: s.prsMerged, issuesOpened: s.issuesOpened, medianDaysToMerge: s.medianDaysToMerge, p90DaysToMerge: s.p90DaysToMerge, openIssues: s.openIssues, openPrs: s.openPrs });
    const allTimeRange = days >= 36500;
    const missing = await missingSources();
    const split = missing.issues ?? {
      core: brief(rangeSummary(core, [], since, until)),
      outside: brief(rangeSummary(outside, [], since, until)),
      corePrevious: allTimeRange ? null : brief(rangeSummary(core, [], prevSince, since)),
      outsidePrevious: allTimeRange ? null : brief(rangeSummary(outside, [], prevSince, since)),
    };
    const known = (o: object, items: string[], byCommits: string[]) => blank(blank(o, missing.issues, items), missing.commits, byCommits);
    return json({
      // The exact window the summary counts, so it can be checked against GitHub search.
      window: { since, until },
      notCollected: missing,
      split,
      botOpenIssues: missing.issues ? null : allItems.filter((i) => i.kind === "issue" && !i.closed_at && isBot(i.author)).length,
      daily: momentumDaily(items, commits, today).map((d) => known(d, ITEM_DAILY, COMMIT_DAILY)),
      summary: known(rangeSummary(items, commits, since, until), ITEM_SUMMARY, COMMIT_SUMMARY),
      // The equal-length window before this one, for trend colours; "all" has none.
      previous: days >= 36500 ? null : known({ ...rangeSummary(items, commits, prevSince, since), mergeDurations: [], closeDurations: [] }, ITEM_SUMMARY, COMMIT_SUMMARY),
      contributors: contributors(allItems, allCommits, weekly, since, firstCommitBy, alias).map((c) =>
        known(c, ["prsOpened", "prsMerged", "issuesOpened"], ["commits", "firstCommit"]),
      ),
      loc,
      locFiltered: filtered,
      people: { exclude: [...people.exclude], bots: people.bots },
    });
  }


  if (path === "/api/ci") {
    base = (await collectedAt("runs")) ?? base;
    const runs = await all<RunRow>(
      "SELECT id, workflow, branch, event, status, conclusion, head_sha, run_attempt, created_at, duration_s FROM workflow_runs WHERE created_at >= ?",
      sinceIso(days),
    );
    const span = await db
      .prepare("SELECT MIN(created_at) AS first, MAX(created_at) AS last FROM workflow_runs")
      .first<{ first: string | null; last: string | null }>();
    return json({
      // What was counted, so scripts/audit_numbers.py can count the same runs on GitHub.
      window: { since: sinceIso(days), until: new Date(base).toISOString() },
      coverage: span,
      build: { branch: PROJECT.defaultBranch, events: BUILD_EVENTS, excluded: CI_EXCLUDE_WORKFLOWS },
      main: ciStats(runs.filter(isDefaultBranchBuild), LONG_RUN_S),
    });
  }

  if (path === "/api/ci-trend") {
    base = (await collectedAt("runs")) ?? base;
    const onlyBuild = url.searchParams.get("branch") !== "all";
    const buildFilter = onlyBuild ? DEFAULT_BRANCH_BUILD_SQL : "1";
    const workflow = url.searchParams.get("workflow") || null;
    // Days; the page groups them into weeks or months itself.
    const period = "substr(created_at,1,10)";
    const rows = await all<{ period: string; success: number; failure: number; cancelled: number; other: number; total: number }>(
      `SELECT ${period} AS period,
         SUM(conclusion = 'success') AS success,
         SUM(conclusion = 'failure') AS failure,
         SUM(conclusion = 'cancelled') AS cancelled,
         SUM(conclusion IS NULL OR conclusion NOT IN ('success','failure','cancelled')) AS other,
         COUNT(*) AS total
       FROM workflow_runs
       WHERE created_at >= ? AND ${buildFilter} AND (? IS NULL OR workflow = ?)
       GROUP BY period ORDER BY period`,
      // Whole days from the start of the first period the page will show (it trims to week or month starts itself).
      sinceDay(days + 31),
      workflow,
      workflow,
    );
    const names = (
      await all<{ workflow: string }>(
        `SELECT workflow, COUNT(*) AS n FROM workflow_runs WHERE created_at >= ? AND ${buildFilter} GROUP BY workflow ORDER BY n DESC`,
        sinceIso(days),
      )
    ).map((r) => r.workflow);
    // Echoed so the page can tell a response for an older menu choice from the current one.
    return json({ rows, workflows: names, branch: url.searchParams.get("branch") ?? "default", workflow: url.searchParams.get("workflow") ?? "" });
  }

  if (path === "/api/ci-time") {
    base = (await collectedAt("runs")) ?? base;
    // Wall-clock time of finished runs, all branches and triggers. Runs that took longer than
    // LONG_RUN_S almost always sat waiting for approval or a runner, so they are counted apart.
    const rows = await all<{ day: string; workflow: string; seconds: number; runs: number }>(
      `SELECT substr(created_at,1,10) AS day, workflow, SUM(duration_s) AS seconds, COUNT(*) AS runs
       FROM workflow_runs WHERE created_at >= ? AND duration_s IS NOT NULL AND duration_s <= ${LONG_RUN_S} GROUP BY day, workflow`,
      sinceDay(days + 31),
    );
    const totals = (from: string, to: string) =>
      all<{ workflow: string; seconds: number; runs: number; long: number }>(
        `SELECT workflow, SUM(CASE WHEN duration_s <= ${LONG_RUN_S} THEN duration_s ELSE 0 END) AS seconds,
           SUM(duration_s <= ${LONG_RUN_S}) AS runs, SUM(duration_s > ${LONG_RUN_S}) AS long
         FROM workflow_runs WHERE created_at >= ? AND created_at < ? AND duration_s IS NOT NULL GROUP BY workflow`,
        from,
        to,
      );
    const first = (await db.prepare("SELECT MIN(created_at) AS first FROM workflow_runs").first<{ first: string | null }>())?.first;
    // Compare only against a window that collection fully covers; a half-empty one would show false growth.
    const covered = days < 36500 && !!first && first <= sinceIso(days * 2);
    return json({
      rows,
      current: await totals(sinceIso(days), "9999"),
      previous: covered ? await totals(sinceIso(days * 2), sinceIso(days)) : null,
      longRunSeconds: LONG_RUN_S,
    });
  }

  if (path === "/api/tests") {
    base = (await collectedAt("tests")) ?? base;
    const rows = await all(
      "SELECT day, passed, failed, skipped, errors, total, jobs FROM test_results WHERE day >= ? ORDER BY day",
      sinceDay(days),
    );
    return json({ rows });
  }

  if (path === "/api/evals") {
    base = (await collectedAt("evals")) ?? base;
    const since = sinceIso(days);
    // Suites with results in range, most-run first: the first is the default view.
    const suites = await all<{ suite: string; runs: number }>(
      `SELECT s.suite, COUNT(DISTINCT s.run_id) AS runs FROM eval_results s JOIN eval_runs r ON r.run_id = s.run_id
       WHERE r.created_at >= ? AND (r.conclusion IS NULL OR r.conclusion != 'cancelled') GROUP BY s.suite ORDER BY runs DESC, s.suite`,
      since,
    );
    const f = parseFilter(url.searchParams, suites.map((s) => s.suite));
    const stored = (await db.prepare("SELECT COUNT(*) AS n FROM eval_runs WHERE files > 0").first<{ n: number }>())?.n ?? 0;
    const RUN_COLS = "r.run_id, r.workflow, r.event, r.ref, r.sha, r.created_at, r.conclusion, r.html_url";
    const RES_COLS = "s.run_id, s.suite, s.tier, s.model, s.category, s.result_id, s.status, s.score, s.elapsed_s, s.cost_usd, s.input_tokens, s.output_tokens, s.note";
    const inRange = await all<EvalRunRow>(
      `SELECT ${RUN_COLS} FROM eval_runs r WHERE r.created_at >= ? AND EXISTS (SELECT 1 FROM eval_results s WHERE s.run_id = r.run_id AND s.suite = ?)`,
      since,
      f.suite,
    );
    // Up to 40 earlier runs of the suite, whatever the range, so the first runs shown have something to compare with.
    const earlier = (
      await all<EvalRunRow>(
        `SELECT ${RUN_COLS} FROM eval_runs r WHERE r.created_at < ? AND (r.conclusion IS NULL OR r.conclusion != 'cancelled')
         AND EXISTS (SELECT 1 FROM eval_results s WHERE s.run_id = r.run_id AND s.suite = ?) ORDER BY r.created_at DESC LIMIT 400`,
        since,
        f.suite,
      )
    )
      .filter((r) => matchesTrigger(r, f.trig))
      .slice(0, 40);
    const rows = await all<EvalResultRow>(
      `SELECT ${RES_COLS} FROM eval_results s JOIN eval_runs r ON r.run_id = s.run_id WHERE s.suite = ? AND r.created_at >= ?`,
      f.suite,
      since,
    );
    if (earlier.length) {
      rows.push(
        ...(await all<EvalResultRow>(
          // One JSON array, not one bound parameter per run: D1 allows about 100 per statement.
          `SELECT ${RES_COLS} FROM eval_results s WHERE s.suite = ? AND s.run_id IN (SELECT value FROM json_each(?))`,
          f.suite,
          JSON.stringify(earlier.map((r) => r.run_id)),
        )),
      );
    }
    return json({ stored, suites, ...evalView([...earlier, ...inRange], rows, f, since, url.searchParams.get("run")) });
  }

  if (path === "/api/traffic") {
    base = (await collectedAt("traffic")) ?? base;
    const span = await db.prepare("SELECT MIN(day) AS first, MAX(day) AS last FROM traffic_daily").first<{ first: string | null; last: string | null }>();
    if (!span?.last) {
      return json({ empty: true, reason: "No traffic collected yet. GitHub keeps only the last 14 days, so this history starts 14 days before the first collection; run the collector (see README)." });
    }
    // Exactly `days` whole days, ending at the last complete day stored.
    const from = new Date(Date.parse(span.last) - (Math.min(days, 36500) - 1) * 86_400_000).toISOString().slice(0, 10);
    const daily = await all<{ day: string; views: number; view_uniques: number; clones: number; clone_uniques: number }>(
      "SELECT day, views, view_uniques, clones, clone_uniques FROM traffic_daily WHERE day >= ? ORDER BY day",
      // From the start of the first whole week or month the page will show.
      sinceDay(days + 31),
    );
    const inWindow = daily.filter((d) => d.day >= from);
    const top = async <T>(table: "traffic_referrers" | "traffic_paths", cols: string) => {
      const day = (await db.prepare(`SELECT MAX(day) AS day FROM ${table}`).first<{ day: string | null }>())?.day ?? null;
      return { day, rows: day ? await all<T>(`SELECT ${cols} FROM ${table} WHERE day = ? ORDER BY count DESC, uniques DESC`, day) : [] };
    };
    const referrers = await top<{ referrer: string; count: number; uniques: number }>("traffic_referrers", "referrer, count, uniques");
    const paths = await top<{ path: string; title: string; count: number; uniques: number }>("traffic_paths", "path, title, count, uniques");
    return json({
      firstDay: span.first,
      lastDay: span.last,
      // Visits, not people: GitHub's uniques are per day and do not add up across days.
      window: { from, to: span.last, days: inWindow.length, views: inWindow.reduce((s, d) => s + d.views, 0), clones: inWindow.reduce((s, d) => s + d.clones, 0) },
      daily,
      referrers,
      paths,
    });
  }

  if (path === "/api/channels") {
    base = (await collectedAt("channels")) ?? base;
    const span = await db.prepare("SELECT MIN(day) AS first, MAX(day) AS last FROM channel_snapshots").first<{ first: string | null; last: string | null }>();
    if (!span?.last) return json({ empty: true, reason: "No install channel readings yet. Run the collector (see README)." });
    // The day before the window too, so its first day has a difference.
    const rows = await all<ChannelRow>("SELECT day, channel, package, kind, value FROM channel_snapshots WHERE day >= ? ORDER BY day", sinceDay(days + 32));
    const latest = await all<ChannelRow>("SELECT day, channel, package, kind, value FROM channel_snapshots WHERE day = ? ORDER BY channel, package", span.last);
    return json({ firstDay: span.first, lastDay: span.last, latest, ...channelView(rows) });
  }

  if (path === "/api/status") {
    // The latest result of every source, then recent history: repeated runs of one source must not hide the others.
    const log = await all<{ finished_at: string; source: string; ok: number; detail: string }>(
      `SELECT finished_at, source, ok, detail FROM collect_log WHERE id IN (SELECT MAX(id) FROM collect_log GROUP BY source)
       UNION SELECT finished_at, source, ok, detail FROM (SELECT * FROM collect_log ORDER BY id DESC LIMIT 40)
       ORDER BY finished_at DESC`,
    );
    // Rows logged under a renamed source's old name count as that source, so a newer result supersedes them.
    return json({ log: log.map((l) => ({ ...l, source: currentSourceName(l.source) })) });
  }

  return json({ error: "Not found" }, 404);
}
