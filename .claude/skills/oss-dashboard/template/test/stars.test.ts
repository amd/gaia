// Star history when the stargazer list cannot be read: nightly repository snapshots, honestly labelled.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleApi } from "../src/api";
import { insertMany, writeAtomic } from "../src/collector";
import { memoryD1 } from "./d1";

const DAY = 86_400_000;
const NOW = new Date("2026-06-15T12:00:00.000Z");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());
const day = (daysAgo: number) => new Date(Date.now() - daysAgo * DAY).toISOString().slice(0, 10);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const get = async (db: D1Database, path: string, q = ""): Promise<any> =>
  (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json();
const put = (db: D1Database, table: string, cols: string[], rows: unknown[][]) => writeAtomic(db, insertMany(db, table, cols, rows));

async function withSnapshots(days: number[]): Promise<D1Database> {
  const db = memoryD1();
  // 1,000 stars on the first snapshot, then 10 more each day.
  await put(db, "repo_snapshots", ["day", "stars", "forks", "open_issues"], days.map((d, i) => [day(d), 1000 + 10 * i, 50, 5]));
  await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [[new Date().toISOString(), "issues", 1, "ok"], [new Date().toISOString(), "commits", 1, "ok"]]);
  return db;
}

describe("star history from snapshots", () => {
  it("traction totals follow the snapshots and say where they come from", async () => {
    const db = await withSnapshots([3, 2, 1]);
    const t = await get(db, "/api/traction");
    expect(t.starHistory).toEqual({ from: "snapshots", since: day(3) });
    expect(t.stars.map((r: { total: number }) => r.total).slice(0, 3)).toEqual([1000, 1010, 1020]);
    // The first snapshot is a starting total: no stars were 'added' that day.
    expect(t.stars.map((r: { added: number | null }) => r.added).slice(0, 3)).toEqual([null, 10, 10]);
  });

  it("gives no star growth for a window that reaches back to the first snapshot, and a real one after", async () => {
    const db = await withSnapshots(Array.from({ length: 40 }, (_, i) => 40 - i));
    const near = await get(db, "/api/growth", "range=7");
    expect(near.current.starGrowth).toBeCloseTo(70 / (1000 + 10 * 32), 5);
    const far = await get(db, "/api/growth", "range=90");
    expect(far.current.starGrowth).toBeNull();
  });

  it("does not hold the growth window back when only the stars source is failing", async () => {
    const db = await withSnapshots([2, 1]);
    await put(db, "collect_log", ["finished_at", "source", "ok", "detail"], [["2026-01-01T00:00:00Z", "stars", 0, "HTTP 404"]]);
    const g = await get(db, "/api/growth", "range=7");
    expect(Date.parse(g.from) + g.days * DAY).toBeGreaterThan(Date.parse(day(1)));
  });
});
