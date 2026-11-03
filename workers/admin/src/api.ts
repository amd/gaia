// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import {
  contributors,
  cumulativeDaily,
  isBot,
  keepAuthor,
  median,
  momentumDaily,
  parsePeople,
  packageDaily,
  rangeSummary,
  type CommitRow,
  type DailyRow,
  type ItemRow,
  type RangeSummary,
} from "./momentum";
import {
  categoryBreakdown,
  filterRuns,
  isSweBench,
  matchesTrigger,
  parseFilter,
  runKind,
  scenarioDetails,
  scenarioReliability,
  summarizeRuns,
  tierLabel,
  type EvalRunRow,
  type EvalScenarioRow,
} from "./evalstats";
import { activity, isExternal, downloadPeriods, downloadWindows, growthPeriods, growthWindow, newcomerRetention, per30, RETURN_DAYS, type DownloadDay } from "./growth";
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

// "Build health on main" counts the runs that test what landed: pushes and the nightly schedule.
// Issue- and comment-triggered automation and hand-dispatched runs on main are left out.
const BUILD_EVENTS = ["push", "schedule"];
const BUILD_EVENTS_SQL = `(${BUILD_EVENTS.map((e) => `'${e}'`).join(", ")})`;
const isBuildOnMain = (r: { branch: string | null; event: string }) => r.branch === "main" && BUILD_EVENTS.includes(r.event);

/** Runs longer than this are treated as having waited (for approval or a runner), not as CI work. */
export const LONG_RUN_S = 6 * 3600;


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
  let base = Date.now();
  const sinceIso = (d: number): string => new Date(base - d * 86_400_000).toISOString();
  const sinceDay = (d: number): string => sinceIso(d).slice(0, 10);

  if (path === "/api/downloads") {
    const latest = await db.prepare("SELECT MAX(day) AS day FROM asset_snapshots").first<{ day: string | null }>();
    if (!latest?.day) return json({ empty: true, reason: "No snapshots yet. Run the collector (see README)." });
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

  if (path === "/api/traction") {
    const stars = await all<DailyRow>("SELECT day, n FROM star_daily ORDER BY day");
    const forks = await all<DailyRow>("SELECT day, n FROM fork_daily ORDER BY day");
    const pkgs = await all<{ source: string; day: string; downloads: number }>(
      "SELECT source, day, downloads FROM package_daily ORDER BY day",
    );
    const repo = await db.prepare("SELECT stars, forks FROM repo_snapshots ORDER BY day DESC LIMIT 1").first<{ stars: number; forks: number }>();
    return json({
      starsHeadline: repo?.stars ?? null,
      forksHeadline: repo?.forks ?? null,
      stars: cumulativeDaily(stars, today),
      forks: cumulativeDaily(forks, today),
      packages: packageDaily(pkgs, today),
    });
  }

  if (path === "/api/growth") {
    // core=alice+alice-work,bob: one entry per person, + joining one person's accounts.
    const groups = (url.searchParams.get("core") ?? "").split(",").map((g) => g.split("+").map((x) => x.trim().toLowerCase()).filter(Boolean)).filter((g) => g.length);
    const maintainers = new Set(groups.flat());
    const alias = new Map(groups.flatMap((g) => g.map((l) => [l, g[0]!] as [string, string])));
    const stars = await all<DailyRow>("SELECT day, n FROM star_daily ORDER BY day");
    const forks = await all<DailyRow>("SELECT day, n FROM fork_daily ORDER BY day");
    const items = await all<ItemRow>("SELECT kind, created_at, closed_at, merged_at, assoc, author FROM items WHERE kind = 'pr'");
    const commits = await all<CommitRow>("SELECT day, at, author FROM commits");
    const shift = (day: string, n: number) => new Date(Date.parse(day) + n * 86_400_000).toISOString().slice(0, 10);
    // Whole days through the last one fully collected: a collection on day D covers the days before it.
    const asOf = await collectedAt("issues", "commits", "stars");
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
    const gran = !allTime && days <= 90 ? "week" : "month";
    // Periods start four before the range, so the page can compare the first ones with a recent pace.
    const baseFrom = shift(from, gran === "week" ? -28 : -124);
    // Daily downloads: GitHub installs from consecutive snapshots, registries as reported.
    const hist = await all<{ day: string; tag: string; asset: string; downloads: number }>(
      "SELECT day, tag, asset, downloads FROM asset_snapshots WHERE day >= ?",
      // A week of slack: registries report late, so their windows end, and start, a few days early.
      allTime ? "0000" : shift(prevFrom < baseFrom ? prevFrom : baseFrom, -8),
    );
    const pkgs = await all<{ source: string; day: string; downloads: number }>("SELECT source, day, downloads FROM package_daily WHERE day >= ?", allTime ? "0000" : shift(prevFrom < baseFrom ? prevFrom : baseFrom, -7));
    const firsts = Object.fromEntries(
      (await all<{ source: string; day: string }>("SELECT source, MIN(day) AS day FROM package_daily GROUP BY source UNION ALL SELECT 'installs', MIN(day) FROM asset_snapshots")).map((r) => [r.source, r.day]),
    );
    // Installs are differences between snapshots, so the first one counts from the day after.
    if (firsts.installs) firsts.installs = shift(firsts.installs, 1);
    const dl = new Map<string, DownloadDay>();
    const at = (day: string) => dl.get(day) ?? dl.set(day, { day, installs: null, pypi: null, npm: null }).get(day)!;
    for (const p of dailyDeltas(hist).points) at(p.day).installs = p.install;
    for (const r of pkgs) if (r.source === "pypi" || r.source === "npm") at(r.day)[r.source] = (at(r.day)[r.source] ?? 0) + r.downloads;
    const dlDays = [...dl.values()].sort((a, b) => a.day.localeCompare(b.day));
    return json({
      from,
      days: span,
      gran,
      current: { ...current, starRate30: per30(current.starGrowth, span), forkRate30: per30(current.forkGrowth, span) },
      previous: previous && { ...previous, starRate30: per30(previous.starGrowth, span), forkRate30: per30(previous.forkGrowth, span) },
      // People whose first PR was 90 to 180 days ago, so every one has had the full 90 days.
      newcomers: newcomerRetention(activity(items, [], alias), shift(today, -2 * RETURN_DAYS), shift(today, -RETURN_DAYS), today),
      periods: growthPeriods(stars, forks, items, commits, maintainers, gran, today, allTime ? from : baseFrom, alias),
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
    const firstCommitBy = new Map<string, string>();
    for (const c of [...allCommits].sort((x, y) => x.day.localeCompare(y.day))) if (!firstCommitBy.has(c.author)) firstCommitBy.set(c.author, c.day);
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
    const split = {
      core: brief(rangeSummary(core, [], since, until)),
      outside: brief(rangeSummary(outside, [], since, until)),
      corePrevious: allTimeRange ? null : brief(rangeSummary(core, [], prevSince, since)),
      outsidePrevious: allTimeRange ? null : brief(rangeSummary(outside, [], prevSince, since)),
    };
    return json({
      split,
      botOpenIssues: allItems.filter((i) => i.kind === "issue" && !i.closed_at && isBot(i.author)).length,
      daily: momentumDaily(items, commits, today),
      summary: rangeSummary(items, commits, since, until),
      // The equal-length window before this one, for trend colours; "all" has none.
      previous: days >= 36500 ? null : { ...rangeSummary(items, commits, prevSince, since), mergeDurations: [], closeDurations: [] },
      contributors: contributors(allItems, allCommits, weekly, since, firstCommitBy),
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
    return json({ main: ciStats(runs.filter(isBuildOnMain), LONG_RUN_S), coverage: span });
  }

  if (path === "/api/ci-trend") {
    base = (await collectedAt("runs")) ?? base;
    const branch = url.searchParams.get("branch") === "all" ? null : "main";
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
       WHERE created_at >= ? AND (? IS NULL OR (branch = ? AND event IN ${BUILD_EVENTS_SQL})) AND (? IS NULL OR workflow = ?)
       GROUP BY period ORDER BY period`,
      // Whole days from the start of the first period the page will show (it trims to week or month starts itself).
      sinceDay(days + 31),
      branch,
      branch,
      workflow,
      workflow,
    );
    const names = (
      await all<{ workflow: string }>(
        `SELECT workflow, COUNT(*) AS n FROM workflow_runs WHERE created_at >= ? AND (? IS NULL OR (branch = ? AND event IN ${BUILD_EVENTS_SQL})) GROUP BY workflow ORDER BY n DESC`,
        sinceIso(days),
        branch,
        branch,
      )
    ).map((r) => r.workflow);
    // Echoed so the page can tell a response for an older menu choice from the current one.
    return json({ rows, workflows: names, branch: url.searchParams.get("branch") ?? "main", workflow: url.searchParams.get("workflow") ?? "" });
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
    const f = parseFilter(url.searchParams);
    const SCEN_COLS =
      "s.run_id, s.category, s.scenario_id, s.status, s.score, s.elapsed_s, s.cost_usd, s.tps, s.ttft_s, s.input_tokens, s.output_tokens, s.tool_calls, s.turns, s.root_cause, s.kind";
    const RUN_COLS = "run_id, workflow, event, ref, is_tag, sha, created_at, conclusion, tier, agent_model, judge_model, minutes, html_url";
    // Every run, with just enough counted to classify it; the comparison to "the previous run" must not depend on the range.
    const allRuns = await all<EvalRunRow & { scen: number; tasks: number }>(
      `SELECT ${RUN_COLS},
         (SELECT COUNT(*) FROM eval_scenarios s WHERE s.run_id = r.run_id AND s.kind != 'task' AND s.status NOT LIKE 'SKIPPED%') AS scen,
         (SELECT COUNT(*) FROM eval_scenarios s WHERE s.run_id = r.run_id AND s.kind = 'task' AND s.status NOT LIKE 'SKIPPED%') AS tasks
       FROM eval_runs r ORDER BY created_at`,
    );
    const comparable = (r: EvalRunRow & { scen: number; tasks: number }) =>
      r.scen + r.tasks > 0 &&
      r.conclusion !== "cancelled" &&
      (f.suite === "swebench") === isSweBench(r) &&
      matchesTrigger(runKind(r), f.trig) &&
      (!f.tier || tierLabel(r, r.scen || r.tasks) === f.tier) &&
      (!f.model || r.agent_model === f.model);
    const since = sinceIso(days);
    const inRange = allRuns.filter((r) => r.created_at >= since);
    const rows = await all<EvalScenarioRow>(
      `SELECT ${SCEN_COLS} FROM eval_scenarios s JOIN eval_runs r ON r.run_id = s.run_id WHERE r.created_at >= ?`,
      since,
    );
    const everyRun = summarizeRuns(inRange, rows);
    // A run's tier is a property of the whole run, so a category filter must not change it.
    const tierOf = new Map(everyRun.map((r) => [r.run_id, r.tierLabel]));
    const shown = filterRuns(
      summarizeRuns(inRange, rows, f.category).map((r) => ({ ...r, tierLabel: tierOf.get(r.run_id) ?? r.tierLabel })),
      f,
    );
    const shownIds = new Set(shown.map((r) => r.run_id));
    const requested = Number(url.searchParams.get("run"));
    const selected = shownIds.has(requested) ? requested : (shown.at(-1)?.run_id ?? 0);
    const selRun = allRuns.find((r) => r.run_id === selected);
    const byCat = (xs: EvalScenarioRow[]) => (f.category ? xs.filter((x) => x.category === f.category) : xs);
    let scenarios: ReturnType<typeof scenarioDetails> = [];
    let categories: ReturnType<typeof categoryBreakdown> = [];
    if (selRun) {
      const selRows = byCat(await all<EvalScenarioRow>(`SELECT ${SCEN_COLS} FROM eval_scenarios s WHERE s.run_id = ?`, selected));
      // Up to 40 earlier comparable runs, whatever the range: enough to find each scenario's last result.
      const earlier = allRuns.filter((r) => r.created_at < selRun.created_at && comparable(r)).slice(-40);
      const earlierRows = earlier.length
        ? byCat(
            await all<EvalScenarioRow>(
              `SELECT ${SCEN_COLS} FROM eval_scenarios s WHERE s.run_id IN (${earlier.map(() => "?").join(",")})`,
              ...earlier.map((r) => r.run_id),
            ),
          )
        : [];
      scenarios = scenarioDetails(selRows, earlier, earlierRows);
      categories = categoryBreakdown(selRows.filter((r) => r.kind !== "task" || selRows.every((x) => x.kind === "task")), earlier, earlierRows);
    }
    const shownRows = byCat(rows.filter((r) => shownIds.has(r.run_id)));
    const measuredRows = shownRows.filter((r) => r.kind !== "task" || f.suite === "swebench");
    // Categories to offer as filter buttons: those measured by the runs the other filters select.
    const baseIds = new Set(filterRuns(everyRun, f).map((r) => r.run_id));
    const catList = [...new Set(rows.filter((r) => baseIds.has(r.run_id) && r.kind !== "task").map((r) => r.category))].sort();
    return json({
      filter: f,
      allRuns: everyRun.map((r) => ({ run_id: r.run_id, workflow: r.workflow, kind: r.kind, tierLabel: r.tierLabel, agent_model: r.agent_model })),
      runs: shown,
      categoriesAvailable: catList,
      selected,
      scenarios,
      categories,
      reliability: scenarioReliability(shown, measuredRows).slice(0, 300),
      medianScenarioSeconds: median(measuredRows.map((r) => r.elapsed_s).filter((x): x is number => x != null)),
    });
  }

  if (path === "/api/status") {
    const log = await all("SELECT finished_at, source, ok, detail FROM collect_log ORDER BY id DESC LIMIT 40");
    return json({ log });
  }

  return json({ error: "Not found" }, 404);
}
