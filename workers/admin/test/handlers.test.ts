// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Endpoint tests against real SQLite: the SQL, the joins and the response shape the pages rely on.

import { describe, expect, it } from "vitest";
import { handleApi } from "../src/api";
import { insertMany, writeAtomic } from "../src/collector";
import { memoryD1 } from "./d1";

const DAY = 86_400_000;
const iso = (daysAgo: number) => new Date(Date.now() - daysAgo * DAY).toISOString();
const day = (daysAgo: number) => iso(daysAgo).slice(0, 10);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const get = async (db: D1Database, path: string, q = ""): Promise<any> =>
  (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json();
const put = (db: D1Database, table: string, cols: string[], rows: unknown[][]) => writeAtomic(db, insertMany(db, table, cols, rows));

describe("/api/ci-trend", () => {
  it("echoes the branch and workflow it answered for, and counts only build runs on main", async () => {
    const db = memoryD1();
    await put(db, "workflow_runs", ["id", "workflow", "branch", "event", "status", "conclusion", "head_sha", "run_attempt", "created_at", "duration_s"], [
      [1, "Unit", "main", "push", "completed", "success", "a", 1, iso(1), 60],
      [2, "Unit", "main", "issues", "completed", "failure", "b", 1, iso(1), 60],
      [3, "Lint", "feature", "push", "completed", "failure", "c", 1, iso(1), 60],
    ]);
    const main = await get(db, "/api/ci-trend", "range=7&branch=main&workflow=Unit");
    expect(main).toMatchObject({ branch: "main", workflow: "Unit", workflows: ["Unit"] });
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
      [day(2), "v1.0.0", "gaia-windows-setup.exe", 100],
      [day(2), "v1.0.0", "latest.yml", 500],
      [day(1), "v1.0.0", "gaia-windows-setup.exe", 130],
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
    const everyone = await get(db, "/api/momentum", "range=30");
    expect(everyone.summary).toMatchObject({ prsMerged: 2, commits: 2, contributors: 2 });
    expect(everyone.previous).toMatchObject({ prsMerged: 1 });
    const noAlice = await get(db, "/api/momentum", "range=30&exclude=Alice");
    expect(noAlice.summary).toMatchObject({ prsMerged: 1, commits: 1, contributors: 1 });
    expect(noAlice.people.exclude).toEqual(["alice"]);
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
    const g = await get(db, "/api/growth", "range=7&core=Alice");
    // Seven whole days ending today: six days ago is in, seven days ago is not.
    expect(g).toMatchObject({ from: day(6), days: 7 });
    expect(g.current).toMatchObject({ stars: 10, starGrowth: 10 / 1100, prsMerged: 2, externalShare: 0.5, active: 2, newContributors: 2 });
    expect(g.previous).toMatchObject({ stars: 1100, starGrowth: null, active: 0 });
    expect(g.periods[0].start >= day(13)).toBe(true);
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
    expect((await get(db, "/api/growth", "range=30")).newcomers).toEqual({ cohort: 2, returned: 1, rate: 0.5 });
  });
});

describe("/api/evals", () => {
  const RUN = ["run_id", "workflow", "event", "ref", "is_tag", "sha", "created_at", "conclusion", "tier", "agent_model", "judge_model", "minutes", "lanes", "html_url", "collected_at", "run_attempt"];
  const SC = ["run_id", "category", "scenario_id", "lane", "status", "score", "elapsed_s", "cost_usd", "tps", "ttft_s", "input_tokens", "output_tokens", "tool_calls", "turns", "root_cause", "kind"];
  const run = (id: number, daysAgo: number, conclusion = "success", tier = "smoke") =>
    [id, "Agent Eval", "schedule", "main", 0, "sha" + id, iso(daysAgo), conclusion, tier, "m", "j", 10, 1, "u", iso(0), 1];
  const sc = (id: number, cat: string, scen: string, status: string) => [id, cat, scen, "l1", status, 8, 30, null, null, null, null, null, null, null, null, "scenario"];

  async function seed() {
    const db = memoryD1();
    await put(db, "eval_runs", RUN, [run(1, 5), run(2, 3, "cancelled"), run(3, 1)]);
    await put(db, "eval_scenarios", SC, [
      sc(1, "rag", "a", "PASS"), sc(1, "code", "b", "PASS"),
      sc(2, "rag", "a", "FAIL"),
      sc(3, "rag", "a", "FAIL"), sc(3, "code", "b", "PASS"),
    ]);
    return db;
  }

  it("leaves cancelled runs out, selects the newest shown run, and compares each scenario with the last comparable run", async () => {
    const e = await get(await seed(), "/api/evals", "range=30");
    expect(e.runs.map((r: { run_id: number }) => r.run_id)).toEqual([1, 3]);
    expect(e.selected).toBe(3);
    expect(e.scenarios.find((s: { scenario_id: string }) => s.scenario_id === "a")).toMatchObject({ status: "FAIL", previous: "PASS", previousRun: 1 });
  });

  it("falls back to the newest run when the requested one is not shown", async () => {
    expect((await get(await seed(), "/api/evals", "range=30&run=2")).selected).toBe(3);
    expect((await get(await seed(), "/api/evals", "range=30&run=1")).selected).toBe(1);
  });

  it("narrows counts to a category without changing a run's tier", async () => {
    const e = await get(await seed(), "/api/evals", "range=30&cat=rag");
    expect(e.runs.map((r: { scenarios: number; tierLabel: string }) => [r.scenarios, r.tierLabel])).toEqual([[1, "smoke"], [1, "smoke"]]);
    expect(e.scenarios).toEqual([expect.objectContaining({ category: "rag" })]);
    expect(e.categoriesAvailable).toEqual(["code", "rag"]);
  });
});
