import { describe, expect, it } from "vitest";
import { handleApi, LONG_RUN_S } from "../src/api";
import { insertMany, writeAtomic } from "../src/collector";
import { collectTests } from "../src/tests";
import { memoryD1 } from "./d1";

const DAY = 86_400_000;
const iso = (daysAgo: number) => new Date(Date.now() - daysAgo * DAY).toISOString();
const get = async (db: D1Database, path: string, q = "") =>
  (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json() as Promise<Record<string, unknown>>;

async function addRuns(db: D1Database, rows: [number, string, string, string, string | null, number, string, number | null][]) {
  // id, workflow, branch, event, conclusion, attempt, created_at, duration
  await writeAtomic(
    db,
    insertMany(
      db,
      "workflow_runs",
      ["id", "workflow", "branch", "event", "status", "conclusion", "head_sha", "run_attempt", "created_at", "duration_s"],
      rows.map(([id, wf, br, ev, c, att, at, d]) => [id, wf, br, ev, "completed", c, `sha${id}`, att, at, d]),
    ),
  );
}

describe("bulk insert against real SQLite", () => {
  it("stores every row with its types, nulls included", async () => {
    const db = memoryD1();
    await addRuns(db, [
      [1, "Unit", "main", "push", "success", 1, iso(1), 60],
      [2, "Unit", "main", "push", null, 1, iso(1), null],
    ]);
    const rows = (await db.prepare("SELECT id, conclusion, duration_s FROM workflow_runs ORDER BY id").all()).results;
    expect(rows).toEqual([
      { id: 1, conclusion: "success", duration_s: 60 },
      { id: 2, conclusion: null, duration_s: null },
    ]);
  });
});

describe("/api/ci", () => {
  it("counts only push and nightly runs on main as build health", async () => {
    const db = memoryD1();
    await addRuns(db, [
      [1, "Unit", "main", "push", "success", 1, iso(1), 60],
      [2, "Unit", "main", "schedule", "failure", 1, iso(1), 60],
      [3, "Bot", "main", "issues", "failure", 1, iso(1), 5],
      [4, "Unit", "main", "pull_request", "failure", 1, iso(1), 60],
      [5, "Unit", "feature", "push", "failure", 1, iso(1), 60],
    ]);
    const ci = (await get(db, "/api/ci", "range=7")) as { main: { workflows: { workflow: string; passed: number; failed: number }[] } };
    expect(ci.main.workflows).toEqual([expect.objectContaining({ workflow: "Unit", passed: 1, failed: 1 })]);
  });

  it("only counts runs inside the window", async () => {
    const db = memoryD1();
    await addRuns(db, [
      [1, "Unit", "main", "push", "success", 1, iso(2), 60],
      [2, "Unit", "main", "push", "failure", 1, iso(10), 60],
    ]);
    const ci = (await get(db, "/api/ci", "range=7")) as { main: { workflows: { passed: number; failed: number }[] } };
    expect(ci.main.workflows[0]).toMatchObject({ passed: 1, failed: 0 });
  });
});

describe("/api/ci-time", () => {
  it("leaves runs that waited longer than the limit out of the time and counts them", async () => {
    const db = memoryD1();
    await addRuns(db, [
      [1, "Unit", "main", "push", "success", 1, iso(1), 600],
      [2, "Unit", "pr", "pull_request", "success", 1, iso(1), LONG_RUN_S + 1],
    ]);
    const t = (await get(db, "/api/ci-time", "range=7")) as { current: { workflow: string; seconds: number; runs: number; long: number }[] };
    expect(t.current).toEqual([{ workflow: "Unit", seconds: 600, runs: 1, long: 1 }]);
  });

  it("gives no previous window when collection does not cover it, instead of showing false growth", async () => {
    const db = memoryD1();
    await addRuns(db, [[1, "Unit", "main", "push", "success", 1, iso(10), 60]]);
    expect((await get(db, "/api/ci-time", "range=7")).previous).toBeNull();
    await addRuns(db, [[2, "Unit", "main", "push", "success", 1, iso(20), 90]]);
    const t = (await get(db, "/api/ci-time", "range=7")) as { previous: { seconds: number }[] };
    expect(t.previous).toEqual([expect.objectContaining({ seconds: 60 })]);
  });
});

describe("/api/momentum open issues", () => {
  it("counts the backlog change from the moment the window began, which the chart's first day-end point is past", async () => {
    const db = memoryD1();
    const until = Date.parse("2026-11-18T22:49:00.000Z");
    const at = (ms: number) => new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");
    const since = until - 7 * DAY;
    // 10 open before the window; 30 filed an hour after it began, 20 of them closed later that day; 2 filed since.
    const rows: unknown[][] = [];
    let n = 1;
    for (let i = 0; i < 10; i++) rows.push([n++, "issue", at(since - 30 * DAY), null, null, "NONE", "u"]);
    for (let i = 0; i < 30; i++) rows.push([n++, "issue", at(since + 3_600_000), i < 20 ? at(since + 7_200_000) : null, null, "NONE", "u"]);
    for (let i = 0; i < 2; i++) rows.push([n++, "issue", at(until - DAY), null, null, "NONE", "u"]);
    await writeAtomic(db, insertMany(db, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], rows));
    await writeAtomic(db, insertMany(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[at(until), "issues", 1, "ok"], [at(until), "commits", 1, "ok"]]));
    const m = (await get(db, "/api/momentum", "range=7")) as { previous: { openIssues: number }; summary: { openIssues: number }; window: { since: string } };
    expect(m.window.since).toBe(new Date(since).toISOString());
    expect([m.previous.openIssues, m.summary.openIssues]).toEqual([10, 22]);
  });
});

describe("/api/status with sources logged under their old names", () => {
  const log = async (rows: unknown[][]) => {
    const db = memoryD1();
    await writeAtomic(db, insertMany(db, "collect_log", ["finished_at", "source", "ok", "detail"], rows));
    return ((await get(db, "/api/status")) as { log: { source: string; ok: number }[] }).log;
  };

  it("reads 'code stats' and 'package history' as code-stats and package-history, so a newer result replaces an old failure", async () => {
    const l = await log([
      ["2026-11-01T03:20:00Z", "code stats", 0, "HTTP 500"],
      ["2026-11-01T03:21:00Z", "package history", 0, "HTTP 500"],
      ["2026-11-02T03:20:00Z", "code-stats", 1, "ok"],
      ["2026-11-02T03:21:00Z", "package-history", 1, "ok"],
    ]);
    expect(l.map((x) => [x.source, x.ok])).toEqual([["package-history", 1], ["code-stats", 1], ["package-history", 0], ["code-stats", 0]]);
    // What the footer does: the newest row per source decides its state.
    const latest = new Map<string, number>();
    for (const x of l) if (!latest.has(x.source)) latest.set(x.source, x.ok);
    expect([...latest]).toEqual([["package-history", 1], ["code-stats", 1]]);
  });
});

describe("/api range parameter", () => {
  it("rejects a range the page never offers", async () => {
    const res = await handleApi(memoryD1(), "/api/ci", new URL("http://localhost/admin/api/ci?range=3"));
    expect(res.status).toBe(400);
  });
});

describe("collectTests", () => {
  const env = (db: D1Database) => ({ DB: db });
  const fetcher = (counts: Record<number, number>, calls: string[]) => async (url: string) => {
    calls.push(url);
    const run = Number(url.match(/runs\/(\d+)\/jobs/)?.[1]);
    if (run) return new Response(JSON.stringify({ jobs: [{ id: run * 10, conclusion: "success" }] }));
    const job = Number(url.match(/jobs\/(\d+)\/logs/)?.[1]);
    return new Response(`===== ${counts[job / 10]} passed in 1.0s =====`);
  };

  it("re-reads a day when a later run finished after the last collection", async () => {
    const db = memoryD1();
    const day = new Date();
    const at = (h: number) => new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h)).toISOString();
    await addRuns(db, [[1, "Tests", "main", "push", "success", 1, at(1), 60]]);
    const calls: string[] = [];
    await collectTests(env(db), fetcher({ 1: 100, 2: 150 }, calls), 2, day, 0);
    await addRuns(db, [[2, "Tests", "main", "push", "success", 1, at(2), 60]]);
    await collectTests(env(db), fetcher({ 1: 100, 2: 150 }, calls), 2, day, 0);
    const row = await db.prepare("SELECT run_id, total FROM test_results").first();
    expect(row).toEqual({ run_id: 2, total: 150 });
    const before = calls.length;
    await collectTests(env(db), fetcher({ 1: 100, 2: 150 }, calls), 2, day, 0);
    expect(calls.length).toBe(before);
  });

  it("keeps the days it could read when another day's log is gone, and reports the failure", async () => {
    const db = memoryD1();
    const now = new Date();
    await addRuns(db, [
      [1, "Tests", "main", "push", "success", 1, iso(0), 60],
      [2, "Tests", "main", "push", "success", 1, iso(1), 60],
    ]);
    const f = async (url: string) => {
      if (url.includes("/runs/2/")) return new Response("", { status: 410 });
      if (url.includes("/runs/1/jobs")) return new Response(JSON.stringify({ jobs: [{ id: 10, conclusion: "success" }] }));
      return new Response("===== 7 passed in 1.0s =====");
    };
    await expect(collectTests(env(db), f, 2, now, 0)).rejects.toThrow(/1 day\(s\) could not be read/);
    expect((await db.prepare("SELECT run_id, total FROM test_results").all()).results).toEqual([{ run_id: 1, total: 7 }]);
  });
});
