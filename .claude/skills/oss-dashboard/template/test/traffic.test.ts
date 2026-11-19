import { describe, expect, it, vi } from "vitest";
import { memoryD1 } from "./d1";

// The page half of this file checks a project with both new sources off; the collector does not read TRAFFIC itself.
vi.mock("../src/config", async (importOriginal) => {
  const real = await importOriginal<typeof import("../src/config")>();
  return { ...real, TRAFFIC: false, CHANNELS: { homebrew: [], homebrewCasks: [], docker: [], rubygems: [], nuget: [], conda: [], packagist: [] } };
});

const { collectTraffic } = await import("../src/traffic");
const { handleApi } = await import("../src/api");
const { SOURCES } = await import("../src/collector");
const { renderUsersPage } = await import("../src/page");

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const day = (d: string, count: number, uniques: number) => ({ timestamp: `${d}T00:00:00Z`, count, uniques });

/** A GitHub that answers the four traffic endpoints with the given lists. */
function github(o: { views: object[]; clones: object[]; referrers?: object[]; paths?: object[] }) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const f = async (url: string, init?: RequestInit) => {
    calls.push({ url, headers: init?.headers as Record<string, string> });
    if (url.includes("/traffic/views")) return ok({ count: 0, uniques: 0, views: o.views });
    if (url.includes("/traffic/clones")) return ok({ count: 0, uniques: 0, clones: o.clones });
    if (url.endsWith("/traffic/popular/referrers")) return ok(o.referrers ?? []);
    if (url.endsWith("/traffic/popular/paths")) return ok(o.paths ?? []);
    throw new Error(`unexpected ${url}`);
  };
  return { f, calls };
}

const rows = async (db: D1Database, sql: string) => (await db.prepare(sql).all()).results;
const get = async (db: D1Database, path: string, q = "") => (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json() as Promise<Record<string, any>>;
const logOk = (db: D1Database, source: string, at: string) =>
  db.prepare("INSERT INTO collect_log (finished_at, source, ok, detail) VALUES (?, ?, 1, 'ok')").bind(at, source).run();

describe("collectTraffic", () => {
  it("stores each day's views and clones, and that night's top referrers and pages", async () => {
    const db = memoryD1();
    const { f, calls } = github({
      views: [day("2026-11-16", 390, 169), day("2026-11-17", 444, 176)],
      clones: [day("2026-11-16", 11631, 337), day("2026-11-17", 11541, 333)],
      referrers: [{ referrer: "github.com", count: 809, uniques: 164 }],
      paths: [{ path: "/acme/widget", title: "Overview", count: 1551, uniques: 700 }],
    });
    expect(await collectTraffic({ DB: db, GITHUB_TOKEN: "t" }, f, "2026-11-18")).toBe("2 days of views and clones (2026-11-16 to 2026-11-17), 1 referrers, 1 pages");
    expect(await rows(db, "SELECT * FROM traffic_daily ORDER BY day")).toEqual([
      { day: "2026-11-16", views: 390, view_uniques: 169, clones: 11631, clone_uniques: 337 },
      { day: "2026-11-17", views: 444, view_uniques: 176, clones: 11541, clone_uniques: 333 },
    ]);
    expect(await rows(db, "SELECT * FROM traffic_referrers")).toEqual([{ day: "2026-11-18", referrer: "github.com", count: 809, uniques: 164 }]);
    expect(await rows(db, "SELECT * FROM traffic_paths")).toEqual([{ day: "2026-11-18", path: "/acme/widget", title: "Overview", count: 1551, uniques: 700 }]);
    // The calls GitHub documents: per-day breakdown, the token, and the repository from config.
    expect(calls.map((c) => c.url)).toEqual([
      "https://api.github.com/repos/example-org/example/traffic/views?per=day",
      "https://api.github.com/repos/example-org/example/traffic/clones?per=day",
      "https://api.github.com/repos/example-org/example/traffic/popular/referrers",
      "https://api.github.com/repos/example-org/example/traffic/popular/paths",
    ]);
    expect(calls[0]!.headers.Authorization).toBe("Bearer t");
  });

  it("never stores the collection day itself: GitHub can list today half-way through it", async () => {
    const db = memoryD1();
    await collectTraffic({ DB: db }, github({ views: [day("2026-11-02", 3, 1), day("2026-11-03", 1, 1)], clones: [day("2026-11-02", 1, 1), day("2026-11-03", 1, 1)] }).f, "2026-11-03");
    expect(await rows(db, "SELECT day FROM traffic_daily ORDER BY day")).toEqual([{ day: "2026-11-02" }]);
  });

  it("overwrites the latest day when GitHub revises it, and keeps days that have left GitHub's 14-day window", async () => {
    const db = memoryD1();
    await collectTraffic({ DB: db }, github({ views: [day("2026-11-01", 5, 2), day("2026-11-02", 3, 1)], clones: [day("2026-11-01", 1, 1), day("2026-11-02", 1, 1)] }).f, "2026-11-03");
    await collectTraffic({ DB: db }, github({ views: [day("2026-11-02", 9, 4), day("2026-11-03", 7, 3)], clones: [day("2026-11-02", 2, 2), day("2026-11-03", 0, 0)] }).f, "2026-11-04");
    expect(await rows(db, "SELECT day, views, view_uniques, clones FROM traffic_daily ORDER BY day")).toEqual([
      { day: "2026-11-01", views: 5, view_uniques: 2, clones: 1 },
      { day: "2026-11-02", views: 9, view_uniques: 4, clones: 2 },
      { day: "2026-11-03", views: 7, view_uniques: 3, clones: 0 },
    ]);
  });

  it("replaces a day's top lists when collected twice that day, and keeps other days' lists", async () => {
    const db = memoryD1();
    const v = { views: [], clones: [] };
    await collectTraffic({ DB: db }, github({ ...v, referrers: [{ referrer: "a.com", count: 1, uniques: 1 }] }).f, "2026-11-17");
    await collectTraffic({ DB: db }, github({ ...v, referrers: [{ referrer: "old.com", count: 1, uniques: 1 }] }).f, "2026-11-18");
    await collectTraffic({ DB: db }, github({ ...v, referrers: [{ referrer: "new.com", count: 2, uniques: 2 }] }).f, "2026-11-18");
    expect(await rows(db, "SELECT day, referrer FROM traffic_referrers ORDER BY day")).toEqual([
      { day: "2026-11-17", referrer: "a.com" },
      { day: "2026-11-18", referrer: "new.com" },
    ]);
  });

  it("leaves out a day only one of the two lists has (they can straddle midnight)", async () => {
    const db = memoryD1();
    await collectTraffic({ DB: db }, github({ views: [day("2026-11-16", 1, 1), day("2026-11-17", 2, 1)], clones: [day("2026-11-17", 3, 1), day("2026-11-18", 4, 1)] }).f, "2026-11-18");
    expect(await rows(db, "SELECT day FROM traffic_daily")).toEqual([{ day: "2026-11-17" }]);
  });

  it("names the token permission on a 403, and writes nothing", async () => {
    const db = memoryD1();
    const f = async () => new Response('{"message":"Resource not accessible by personal access token"}', { status: 403 });
    const err = await collectTraffic({ DB: db }, f, "2026-11-18").catch((e: Error) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toContain('the repository permission "Administration" set to Read-only');
    expect((err as Error).message).toContain("push access to example-org/example");
    expect((err as Error).message).toContain("TRAFFIC = false");
    expect(await rows(db, "SELECT * FROM traffic_daily")).toEqual([]);
  });

  it("calls a 403 with no requests left the rate limit, not a permission problem", async () => {
    const f = async () => new Response("{}", { status: 403, headers: { "x-ratelimit-remaining": "0" } });
    await expect(collectTraffic({ DB: memoryD1() }, f, "2026-11-18")).rejects.toThrow(/rate limit/);
    await expect(collectTraffic({ DB: memoryD1() }, f, "2026-11-18")).rejects.not.toThrow(/Administration/);
  });

  it("refuses a response it does not recognise instead of storing zeros, and writes nothing", async () => {
    const db = memoryD1();
    await expect(collectTraffic({ DB: db }, github({ views: [{ timestamp: "2026-11-17T00:00:00Z", count: null, uniques: 1 }], clones: [] }).f, "2026-11-18")).rejects.toThrow(/without a timestamp, count and uniques/);
    const missing = async (url: string) => (url.includes("/views") ? ok({ count: 1 }) : ok([]));
    await expect(collectTraffic({ DB: db }, missing, "2026-11-18")).rejects.toThrow(/no daily list/);
    await expect(collectTraffic({ DB: db }, github({ views: [], clones: [], referrers: [{ referrer: 5, count: 1, uniques: 1 }] }).f, "2026-11-18")).rejects.toThrow(/referrer entry/);
    expect(await rows(db, "SELECT * FROM traffic_daily")).toEqual([]);
    expect(await rows(db, "SELECT * FROM traffic_referrers")).toEqual([]);
  });
});

describe("/api/traffic", () => {
  it("says history starts 14 days before the first collection when there is none", async () => {
    const r = await get(memoryD1(), "/api/traffic");
    expect(r.empty).toBe(true);
    expect(r.reason).toMatch(/starts 14 days before the first collection/);
  });

  it("sums visits over the window ending at the last successful collection, leaves a missed day out, and lists only the latest top lists", async () => {
    const db = memoryD1();
    const at = "2026-11-18T03:20:00.000Z";
    await logOk(db, "traffic", at);
    // 2026-11-14 was never collected: it must stay missing, not become a zero.
    const days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-15", "2026-11-16", "2026-11-17"];
    for (const [i, d] of days.entries()) {
      await db.prepare("INSERT INTO traffic_daily VALUES (?, ?, ?, ?, ?)").bind(d, 10 + i, 5, 100, 50).run();
    }
    await db.prepare("INSERT INTO traffic_referrers VALUES ('2026-11-17', 'old.com', 99, 9)").run();
    await db.prepare("INSERT INTO traffic_referrers VALUES ('2026-11-18', 'b.com', 3, 2)").run();
    await db.prepare("INSERT INTO traffic_referrers VALUES ('2026-11-18', '<a.com>', 7, 5)").run();
    await db.prepare("INSERT INTO traffic_paths VALUES ('2026-11-18', '/x', 'X', 4, 1)").run();
    const r = await get(db, "/api/traffic", "range=7");
    // range=7 from 2026-11-18T03:20: days 11-11 .. 11-17, of which 11-14 is missing.
    expect(r.window).toEqual({ from: "2026-11-11", to: "2026-11-17", days: 6, views: 12 + 13 + 14 + 15 + 16 + 17, clones: 600 });
    expect(r.daily.map((d: { day: string }) => d.day)).not.toContain("2026-11-14");
    expect(Object.keys(r.daily[0])).toEqual(["day", "views", "view_uniques", "clones", "clone_uniques"]);
    expect(r.firstDay).toBe("2026-11-09");
    expect(r.lastDay).toBe("2026-11-17");
    expect(r.referrers).toEqual({ day: "2026-11-18", rows: [{ referrer: "<a.com>", count: 7, uniques: 5 }, { referrer: "b.com", count: 3, uniques: 2 }] });
    expect(r.paths).toEqual({ day: "2026-11-18", rows: [{ path: "/x", title: "X", count: 4, uniques: 1 }] });
    // No summed "unique visitors" over the window: uniques do not add across days.
    expect(Object.keys(r.window)).not.toContain("uniques");
  });
});

describe("a project with traffic and channels off", () => {
  const html = renderUsersPage();
  it("collects neither", () => {
    expect(SOURCES).not.toContain("traffic");
    expect(SOURCES).not.toContain("channels");
  });
  it("has neither card, its scripts parse, and asks for neither endpoint", () => {
    expect(html).not.toContain('id="traffic"');
    expect(html).not.toContain('id="ch-totals"');
    expect(html).not.toContain('<span class="t">Other install channels');
    expect(html).not.toContain('<span class="t">Repository traffic');
    expect(html).toContain("TRAFFIC=false");
    expect(html).toContain("CHANNELS=[]");
    for (const [, s] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) expect(() => new Function(s!)).not.toThrow();
  });
});
