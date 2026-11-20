// Endpoint tests against real SQLite: the SQL, the joins and the response shape the pages rely on.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleApi } from "../src/api";
import { insertMany, writeAtomic } from "../src/collector";
import { memoryD1 } from "./d1";

const DAY = 86_400_000;
// Freeze the clock the API reads too, so a run near midnight UTC can't split "today" between test and code.
const NOW = new Date("2026-06-15T12:00:00.000Z");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});
const iso = (daysAgo: number) => new Date(Date.now() - daysAgo * DAY).toISOString();
const day = (daysAgo: number) => iso(daysAgo).slice(0, 10);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const get = async (db: D1Database, path: string, q = ""): Promise<any> =>
  (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json();
const put = (db: D1Database, table: string, cols: string[], rows: unknown[][]) => writeAtomic(db, insertMany(db, table, cols, rows));
/** Issues and commits collected successfully at `at` (now by default); without it their figures are "not collected". */
const collected = (db: D1Database, at = iso(0)) => put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[at, "issues", 1, "ok"], [at, "commits", 1, "ok"]]);
// A collection just after midnight tonight: every day through today is complete.
const tonight = () => `${day(-1)}T00:00:00.000Z`;

describe("/api/ci-trend", () => {
  it("echoes the branch and workflow it answered for, and counts only build runs on main", async () => {
    const db = memoryD1();
    await put(db, "workflow_runs", ["id", "workflow", "branch", "event", "status", "conclusion", "head_sha", "run_attempt", "created_at", "duration_s"], [
      [1, "Unit", "main", "push", "completed", "success", "a", 1, iso(1), 60],
      [2, "Unit", "main", "issues", "completed", "failure", "b", 1, iso(1), 60],
      [3, "Lint", "feature", "push", "completed", "failure", "c", 1, iso(1), 60],
    ]);
    const main = await get(db, "/api/ci-trend", "range=7&branch=default&workflow=Unit");
    expect(main).toMatchObject({ branch: "default", workflow: "Unit", workflows: ["Unit"] });
    expect(main.rows).toEqual([expect.objectContaining({ success: 1, failure: 0, total: 1 })]);
    const all = await get(db, "/api/ci-trend", "range=7&branch=all&workflow=");
    expect(all).toMatchObject({ branch: "all", workflow: "" });
    expect(all.rows[0]).toMatchObject({ success: 1, failure: 2, total: 3 });
  });
});

describe("/api/downloads", () => {
  it("says why it is empty before the first snapshot", async () => {
    expect(await get(memoryD1(), "/api/downloads", "range=30")).toMatchObject({ empty: true });
  });

  it("totals installs per release from the latest snapshot, keeps update checks apart, and derives daily downloads", async () => {
    const db = memoryD1();
    await put(db, "releases", ["tag", "published_at", "prerelease", "html_url"], [["v1.0.0", iso(20), 0, "u"]]);
    await put(db, "asset_snapshots", ["day", "tag", "asset", "downloads"], [
      [day(2), "v1.0.0", "App-Setup.exe", 100],
      [day(2), "v1.0.0", "latest.yml", 500],
      [day(1), "v1.0.0", "App-Setup.exe", 130],
      [day(1), "v1.0.0", "latest.yml", 900],
    ]);
    const d = await get(db, "/api/downloads", "range=30");
    expect(d.releases).toEqual([expect.objectContaining({ tag: "v1.0.0", install: 130, meta: 900, byOs: { Windows: 130 } })]);
    expect(d.daily).toEqual([expect.objectContaining({ day: day(1), install: 30, meta: 400 })]);
    expect(d.firstSnapshotDay).toBe(day(2));
  });
});

describe("/api/traction", () => {
  it("returns GitHub's headline counts and the dated running totals", async () => {
    const db = memoryD1();
    await put(db, "repo_snapshots", ["day", "stars", "forks", "open_issues"], [[day(0), 12, 3, 1]]);
    await put(db, "star_daily", ["day", "n"], [[day(3), 10], [day(1), 2]]);
    const t = await get(db, "/api/traction", "range=30");
    expect(t).toMatchObject({ starsHeadline: 12, forksHeadline: 3 });
    expect(t.stars.at(-1)).toMatchObject({ day: day(0), total: 12 });
    expect(t.stars.find((r: { day: string }) => r.day === day(1))).toMatchObject({ added: 2, total: 12 });
  });
});

describe("/api/momentum", () => {
  it("leaves hidden people out of every number, and compares with the window before", async () => {
    const db = memoryD1();
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [
      [1, "pr", iso(3), iso(2), iso(2), "MEMBER", "alice"],
      [2, "pr", iso(3), iso(2), iso(2), "NONE", "bob"],
      [3, "pr", iso(40), iso(39), iso(39), "NONE", "bob"],
    ]);
    await put(db, "commits", ["sha", "day", "author", "at"], [["a", day(2), "alice", iso(2)], ["b", day(2), "bob", iso(2)]]);
    await collected(db);
    const everyone = await get(db, "/api/momentum", "range=30");
    expect(everyone.summary).toMatchObject({ prsMerged: 2, commits: 2, contributors: 2 });
    expect(everyone.previous).toMatchObject({ prsMerged: 1 });
    const noAlice = await get(db, "/api/momentum", "range=30&exclude=Alice");
    expect(noAlice.summary).toMatchObject({ prsMerged: 1, commits: 1, contributors: 1 });
    expect(noAlice.people.exclude).toEqual(["alice"]);
  });
});

describe("windows end at the last collection", () => {
  it("counts the last N days up to when the data was collected, not up to now", async () => {
    const db = memoryD1();
    // Collected 2 days ago; a PR merged 8 days ago is inside "the last 7 days" of that data, though not of the 7 days before now.
    await collected(db, iso(2));
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [[1, "pr", iso(8.5), iso(8), iso(8), "NONE", "carol"]]);
    expect((await get(db, "/api/momentum", "range=7")).summary.prsMerged).toBe(1);
  });
});

describe("/api/momentum core and outside split", () => {
  it("splits people's PRs by core team and outside, ignores the people filter, and counts open bot issues", async () => {
    const db = memoryD1();
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [
      [1, "pr", iso(3), iso(2), iso(2), "CONTRIBUTOR", "alice"],
      [2, "pr", iso(4), iso(2), iso(2), "NONE", "carol"],
      [3, "pr", iso(3), iso(2), iso(2), "NONE", "dependabot[bot]"],
      [4, "issue", iso(3), null, null, "NONE", "github-actions[bot]"],
      [5, "issue", iso(3), null, null, "NONE", "carol"],
    ]);
    await collected(db);
    const m = await get(db, "/api/momentum", "range=30&core=Alice&exclude=alice");
    expect(m.split.core).toMatchObject({ prsMerged: 1, medianDaysToMerge: 1 });
    expect(m.split.outside).toMatchObject({ prsMerged: 1, medianDaysToMerge: 2, openIssues: 1 });
    expect(m.botOpenIssues).toBe(1);
  });
});

describe("/api/growth", () => {
  it("counts maintainers as core even when GitHub labels them contributors, and compares with the window before", async () => {
    const db = memoryD1();
    await put(db, "star_daily", ["day", "n"], [[day(10), 100], [day(7), 1000], [day(6), 4], [day(5), 6]]);
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [
      [1, "pr", iso(4), iso(3), iso(3), "CONTRIBUTOR", "alice"],
      [2, "pr", iso(4), iso(3), iso(3), "FIRST_TIME_CONTRIBUTOR", "carol"],
    ]);
    await put(db, "commits", ["sha", "day", "author", "at"], [["a", day(3), "alice", iso(3)]]);
    await collected(db, tonight());
    const g = await get(db, "/api/growth", "range=7&core=Alice");
    // Seven whole days ending today: six days ago is in, seven days ago is not.
    expect(g).toMatchObject({ from: day(6), days: 7 });
    expect(g.current).toMatchObject({ stars: 10, starGrowth: 10 / 1100, prsMerged: 2, externalShare: 0.5, active: 2, newContributors: 2 });
    expect(g.previous).toMatchObject({ stars: 1100, starGrowth: null, active: 0 });
    // Periods reach back before the range (here to the first data, 10 days ago), for the page's momentum baseline.
    expect(g.periods[0].start <= day(10)).toBe(true);
    const all = await get(db, "/api/growth", "range=all");
    expect(all).toMatchObject({ from: day(10), days: 11, previous: null, gran: "month" });
    expect((await get(db, "/api/growth", "range=1")).days).toBe(2);
    expect(g.gran).toBe("week");
    expect((await get(db, "/api/growth", "range=7")).current.externalShare).toBe(1);
  });
});

describe("/api/growth newcomer retention", () => {
  it("does not count a newcomer's own PR landing as a commit as coming back", async () => {
    const db = memoryD1();
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [
      [1, "pr", iso(120), iso(117), iso(117), "NONE", "erin"],
      [2, "pr", iso(130), iso(129), iso(129), "NONE", "finn"],
      [3, "pr", iso(110), null, null, "NONE", "finn"],
    ]);
    await put(db, "commits", ["sha", "day", "author", "at"], [["e", day(117), "erin", iso(117)]]);
    await collected(db);
    expect((await get(db, "/api/growth", "range=30")).newcomers).toEqual({ cohort: 2, returned: 1, rate: 0.5 });
  });
});

describe("issues or commits never collected", () => {
  const ISSUES = "Issues and pull requests have not been collected yet: GET https://api.github.com/repos/o/r/issues returned HTTP 403.";
  async function seed() {
    const db = memoryD1();
    await put(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], [[1, "pr", iso(3), iso(2), iso(2), "NONE", "bob"]]);
    await put(db, "commits", ["sha", "day", "author", "at"], [["a", day(2), "alice", iso(2)]]);
    // Issues failed on every run so far (the latest error is the one shown); commits never ran at all.
    await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [
      [iso(2), "issues", 0, "GET https://api.github.com/repos/o/r/issues returned HTTP 500."],
      [iso(1), "issues", 0, "GET https://api.github.com/repos/o/r/issues returned HTTP 403."],
    ]);
    return db;
  }
  const empty = (reason: string) => ({ empty: true, reason });
  const COMMITS = "Commits have not been collected yet. Run the collector (see README).";

  it("momentum: issue, PR and commit figures are an empty state with the reason, never zero", async () => {
    const m = await get(await seed(), "/api/momentum", "range=30");
    expect(m.notCollected).toEqual({ issues: empty(ISSUES), commits: empty(COMMITS) });
    expect(m.summary).toMatchObject({ prsOpened: null, prsMerged: null, issuesOpened: null, openIssues: null, openPrs: null, commits: null, contributors: null });
    expect(m.previous).toMatchObject({ prsMerged: null, openIssues: null, commits: null });
    expect(m.split).toEqual(empty(ISSUES));
    expect(m.botOpenIssues).toBeNull();
    expect(m.daily.every((d: Record<string, unknown>) => d.issuesOpened === null && d.openPrs === null && d.commits === null)).toBe(true);
    for (const c of m.contributors) expect(c).toMatchObject({ prsOpened: null, prsMerged: null, issuesOpened: null, commits: null });
    expect(m.contributors.length).toBeGreaterThan(0);
  });

  it("momentum: only the source that was never collected goes blank", async () => {
    const db = await seed();
    await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[iso(0), "commits", 1, "ok"]]);
    const m = await get(db, "/api/momentum", "range=30");
    expect(m.notCollected).toEqual({ issues: empty(ISSUES), commits: null });
    expect(m.summary).toMatchObject({ prsMerged: null, commits: 1, contributors: 1 });
  });

  it("growth: PR-based figures and rates are null, newcomer retention an empty state; stars and forks unaffected", async () => {
    const db = await seed();
    await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[iso(0), "commits", 1, "ok"]]);
    await put(db, "star_daily", ["day", "n"], [[day(40), 100], [day(3), 10]]);
    const g = await get(db, "/api/growth", "range=30");
    expect(g.notCollected).toEqual({ issues: empty(ISSUES), commits: null });
    expect(g.current).toMatchObject({ prsMerged: null, externalShare: null, active: null, newContributors: null, busFactor: 1, stars: 10 });
    expect(g.previous).toMatchObject({ prsMerged: null, externalShare: null, active: null });
    expect(g.newcomers).toEqual(empty(ISSUES));
    expect(g.periods.length).toBeGreaterThan(0);
    for (const p of g.periods) expect(p).toMatchObject({ prsMerged: null, externalMerged: null, newcomerCohort: null, active: null, newContributors: null });
  });

  it("growth: commits never collected leaves the bus factor unknown", async () => {
    const db = await seed();
    await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[iso(0), "issues", 1, "ok"]]);
    const g = await get(db, "/api/growth", "range=30");
    expect(g.notCollected).toEqual({ issues: null, commits: empty(COMMITS) });
    expect(g.current).toMatchObject({ busFactor: null, active: null, prsMerged: 1 });
  });
});

describe("/api/evals", () => {
  const RUN = ["run_id", "run_attempt", "workflow", "event", "ref", "sha", "created_at", "conclusion", "html_url", "files", "collected_at"];
  const RES = ["run_id", "suite", "tier", "model", "category", "result_id", "status", "score", "elapsed_s", "cost_usd", "input_tokens", "output_tokens", "note"];
  const run = (id: number, daysAgo: number, conclusion = "success", event = "schedule", ref = "main") =>
    [id, 1, "Bench", event, ref, "sha" + id, iso(daysAgo), conclusion, "u", 1, iso(0)];
  const res = (id: number, suite: string, cat: string, rid: string, status: string, model = "m") => [id, suite, "smoke", model, cat, rid, status, null, 30, null, null, null, null];

  async function seed() {
    const db = memoryD1();
    await put(db, "eval_runs", RUN, [run(1, 5), run(2, 3, "cancelled"), run(3, 1), run(4, 2, "success", "pull_request", "feature"), run(5, 60)]);
    await put(db, "eval_results", RES, [
      res(5, "bench", "rag", "a", "FAIL"),
      res(1, "bench", "rag", "a", "PASS"), res(1, "bench", "code", "b", "PASS"), res(1, "other", "", "z", "PASS"),
      res(2, "bench", "rag", "a", "FAIL"),
      res(3, "bench", "rag", "a", "FAIL"), res(3, "bench", "code", "b", "PASS"), res(3, "bench", "code", "b", "ERROR", "m2"),
      res(4, "bench", "rag", "a", "PASS"),
    ]);
    return db;
  }

  it("says when nothing has been collected", async () => {
    expect(await get(memoryD1(), "/api/evals", "range=30")).toMatchObject({ stored: 0, suites: [], suite: "", runs: [], selected: null });
  });

  it("defaults to the most-run suite, leaves cancelled and PR runs out, and compares with the last comparable run", async () => {
    const e = await get(await seed(), "/api/evals", "range=30");
    expect(e.suites).toEqual([{ suite: "bench", runs: 3 }, { suite: "other", runs: 1 }]);
    expect(e.suite).toBe("bench");
    expect(e.runs.map((r: { key: string }) => r.key)).toEqual(["1.0", "3.0", "3.1"]);
    expect(e.selected).toBe("3.1");
    const a = await get(await seed(), "/api/evals", "range=30&run=3.0");
    expect(a.results.find((s: { result_id: string }) => s.result_id === "a")).toMatchObject({ status: "FAIL", previous: "PASS", previousRun: 1 });
    // Run 1 is compared with run 5, which is outside the range.
    expect(a.runs.map((r: { key: string; regressed: number | null; fixed: number | null }) => [r.key, r.regressed, r.fixed])).toEqual([["1.0", 0, 1], ["3.0", 1, 0], ["3.1", null, null]]);
  });

  it("serves another suite, and pull requests when asked", async () => {
    expect((await get(await seed(), "/api/evals", "range=30&suite=other")).runs.map((r: { run_id: number }) => r.run_id)).toEqual([1]);
    expect((await get(await seed(), "/api/evals", "range=30&trig=pr")).runs.map((r: { run_id: number }) => r.run_id)).toEqual([4]);
  });

  it("narrows counts to a category and filters by model", async () => {
    const e = await get(await seed(), "/api/evals", "range=30&cat=rag&model=m");
    expect(e.runs.map((r: { key: string; results: number }) => [r.key, r.results])).toEqual([["1.0", 1], ["3.0", 1]]);
    expect(e.options).toEqual({ tiers: ["smoke"], models: ["m", "m2"], categories: ["code", "rag"] });
  });
});
