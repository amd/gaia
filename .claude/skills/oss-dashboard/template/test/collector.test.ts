import { describe, expect, it } from "vitest";
import { collectAll, CollectError, collectReleases, collectRuns, insertMany, SOURCES, unknownSources, withRetries, type CollectEnv } from "../src/collector";
import { NETWORK_RETRIES } from "../src/config";

interface Stmt {
  sql: string;
  args: unknown[];
}

/** Records statements; a bulk json_each INSERT is recorded as one entry per row so assertions read per row. */
function fakeDb(): { db: D1Database; stmts: Stmt[]; batches: number[] } {
  const stmts: Stmt[] = [];
  const batches: number[] = [];
  const record = (sql: string, args: unknown[]) => {
    if (/FROM json_each\(\?\)$/.test(sql)) {
      for (const row of JSON.parse(args[0] as string) as unknown[][]) stmts.push({ sql, args: row });
      return;
    }
    stmts.push({ sql, args });
  };
  const make = (sql: string, args: unknown[] = []) => ({
    sql,
    args,
    bind: (...a: unknown[]) => make(sql, a),
    run: async () => record(sql, args),
    all: async () => ({ results: [] }),
    first: async () => null,
  });
  const db = {
    prepare: (sql: string) => make(sql),
    batch: async (list: { sql: string; args: unknown[] }[]) => {
      batches.push(list.length);
      for (const s of list) record(s.sql, s.args);
    },
  } as unknown as D1Database;
  return { db, stmts, batches };
}

describe("collectReleases atomicity", () => {
  it("writes a day's snapshot in a single batch, replacing that day, so it is never half-stored", async () => {
    const { db, stmts, batches } = fakeDb();
    const assets = Array.from({ length: 300 }, (_, i) => ({ name: `f${i}.exe`, download_count: i }));
    const f = async () => ok([{ tag_name: "v1.0.0", draft: false, prerelease: false, published_at: "2026-10-01T00:00:00Z", html_url: "u", assets }]);
    await collectReleases({ DB: db }, f, "2026-10-26");
    expect(batches).toHaveLength(1);
    expect(stmts[0]!.sql).toBe("DELETE FROM asset_snapshots WHERE day = ?");
    expect(stmts.filter((s) => s.sql.startsWith("INSERT OR REPLACE INTO asset_snapshots"))).toHaveLength(300);
  });
});

describe("insertMany", () => {
  it("binds rows as one JSON array per statement, keeping every value and its type", () => {
    const { db } = fakeDb();
    const rows = Array.from({ length: 2500 }, (_, i) => [i, `t${i}`, i * 2, null]);
    const stmts = insertMany(db, "x", ["a", "b", "c", "d"], rows) as unknown as { sql: string; args: unknown[] }[];
    expect(stmts).toHaveLength(1);
    expect(stmts[0]!.args).toHaveLength(1);
    expect(JSON.parse(stmts[0]!.args[0] as string)).toEqual(rows);
    expect(stmts[0]!.sql).toBe(
      "INSERT OR REPLACE INTO x (a, b, c, d) SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]'), json_extract(value, '$[3]') FROM json_each(?)",
    );
  });

  it("splits into more statements only when one JSON value would get too large", () => {
    const { db } = fakeDb();
    const big = "x".repeat(10_000);
    const stmts = insertMany(db, "x", ["a"], Array.from({ length: 250 }, () => [big]));
    expect(stmts.length).toBe(3);
  });

  it("sizes statements in bytes, so multi-byte text cannot overflow the limit", () => {
    const { db } = fakeDb();
    const wide = "é".repeat(5_000); // 5,000 characters, 10,000 bytes
    const stmts = insertMany(db, "x", ["a"], Array.from({ length: 150 }, () => [wide])) as unknown as { args: string[] }[];
    expect(stmts.length).toBe(2);
    for (const s of stmts) expect(new TextEncoder().encode(s.args[0]).length).toBeLessThanOrEqual(1_000_000);
  });

  it("writes nothing for no rows", () => {
    const { db } = fakeDb();
    expect(insertMany(db, "x", ["a"], [])).toEqual([]);
  });
});

const env = (db: D1Database): CollectEnv => ({
  DB: db,
});

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe("collectReleases", () => {
  it("stores releases and per-asset counts, skipping drafts", async () => {
    const { db, stmts } = fakeDb();
    const f = async () =>
      ok([
        { tag_name: "v1.0.0", draft: false, prerelease: false, published_at: "2026-10-01T00:00:00Z", html_url: "u", assets: [{ name: "a.exe", download_count: 5 }] },
        { tag_name: "v2.0.0", draft: true, prerelease: false, published_at: null, html_url: "u", assets: [{ name: "b.exe", download_count: 9 }] },
      ]);
    const detail = await collectReleases(env(db), f, "2026-10-26");
    expect(detail).toBe("1 releases, 1 assets");
    const snaps = stmts.filter((s) => s.sql.startsWith("INSERT OR REPLACE INTO asset_snapshots"));
    expect(snaps).toHaveLength(1);
    expect(snaps[0]!.args).toEqual(["2026-10-26", "v1.0.0", "a.exe", 5]);
  });

  it("fails with an actionable message on a rate-limit response", async () => {
    const { db } = fakeDb();
    const f = async () => new Response("{}", { status: 403 });
    await expect(collectReleases(env(db), f, "2026-10-26")).rejects.toThrow(/GITHUB_TOKEN/);
  });
});

describe("collectRuns", () => {
  const run = (id: number, status = "completed") => ({
    id, name: "Unit", head_branch: "main", event: "push", status, conclusion: status === "completed" ? "success" : null,
    head_sha: "abc", run_attempt: 1, created_at: "2026-10-26T10:00:00Z", run_started_at: "2026-10-26T10:00:00Z", updated_at: "2026-10-26T10:02:00Z",
  });
  const rangeOf = (u: string) => decodeURIComponent(new URL(u).searchParams.get("created")!).split("..");

  it("queries one UTC day per window, newest first", async () => {
    const { db } = fakeDb();
    const urls: string[] = [];
    const f = async (u: string) => {
      urls.push(u);
      return ok({ total_count: 0, workflow_runs: [] });
    };
    await collectRuns(env(db), f, 3, new Date("2026-10-26T12:00:00Z"));
    expect(urls.map((u) => rangeOf(u)[0])).toEqual(["2026-10-26T00:00:00Z", "2026-10-25T00:00:00Z", "2026-10-24T00:00:00Z"]);
  });

  it("can skip recent days so a long backfill can run in chunks", async () => {
    const { db } = fakeDb();
    const urls: string[] = [];
    const f = async (u: string) => {
      urls.push(u);
      return ok({ total_count: 0, workflow_runs: [] });
    };
    await collectRuns(env(db), f, 1, new Date("2026-10-26T12:00:00Z"), 5);
    expect(rangeOf(urls[0]!)[0]).toBe("2026-10-21T00:00:00Z");
  });

  it("derives duration for completed runs and leaves it null for running ones", async () => {
    const { db, stmts } = fakeDb();
    const f = async () => ok({ total_count: 2, workflow_runs: [run(1), run(2, "in_progress")] });
    await collectRuns(env(db), f, 1, new Date("2026-10-26T12:00:00Z"));
    const rows = stmts.filter((s) => s.sql.includes("workflow_runs"));
    expect(rows[0]!.args.at(-1)).toBe(120);
    expect(rows[1]!.args.at(-1)).toBeNull();
  });

  it("splits a window that exceeds the 1,000-run API cap and keeps every run", async () => {
    const { db, stmts } = fakeDb();
    const f = async (u: string) => {
      const [a, b] = rangeOf(u);
      const wide = Date.parse(b!) - Date.parse(a!) > 40_000_000;
      return wide
        ? ok({ total_count: 1500, workflow_runs: [] })
        : ok({ total_count: 3, workflow_runs: [run(Date.parse(a!) / 1000)] });
    };
    await collectRuns(env(db), f, 1, new Date("2026-10-26T12:00:00Z"));
    expect(stmts.filter((s) => s.sql.includes("workflow_runs")).length).toBeGreaterThan(1);
  });

  it("fails when a window cannot be split any further", async () => {
    const { db } = fakeDb();
    const f = async () => ok({ total_count: 5000, workflow_runs: [] });
    await expect(collectRuns(env(db), f, 1, new Date("2026-10-26T12:00:00Z"))).rejects.toThrow(/1,000/);
  });
});

describe("collectAll", () => {
  it("keeps collecting the other sources when one fails, then reports the failure", async () => {
    const { db, stmts } = fakeDb();
    const f = async (u: string) => {
      if (u.includes("/releases")) return new Response("boom", { status: 500 });
      if (u.includes("/actions/runs")) return ok({ total_count: 0, workflow_runs: [] });
      if (new URL(u).host === "api.npmjs.org" && u.includes("/range/")) return ok({ downloads: [{ day: "2026-10-25", downloads: 1 }] });
      if (new URL(u).host === "api.npmjs.org") return ok({ downloads: 3 });
      if (new URL(u).host === "pypistats.org" && u.includes("/overall")) return ok({ data: [{ category: "without_mirrors", date: "2026-10-25", downloads: 2 }] });
      if (new URL(u).host === "pypistats.org") return ok({ data: { last_week: 4 } });
      if (u.includes("/stargazers") || u.includes("/forks") || u.includes("/issues") || u.includes("/commits")) return ok([]);
      if (u.includes("/actions/workflows/")) return ok({ workflow_runs: [] });
      if (u.includes("/stats/")) return ok([]);
      if (u.includes("/traffic/")) return ok(u.includes("/popular/") ? [] : { views: [], clones: [] });
      return ok({ stargazers_count: 1, forks_count: 2, open_issues_count: 3 });
    };
    await expect(collectAll(env(db), { runDays: 1, fetcher: f, now: new Date("2026-10-26T12:00:00Z") })).rejects.toThrow(/collector:releases/);
    const logs = stmts.filter((s) => s.sql.includes("collect_log")).map((s) => [s.args[1], s.args[2]]);
    expect(logs).toEqual([["releases", 0], ["repo", 1], ["stars", 1], ["forks", 1], ["issues", 1], ["commits", 1], ["code-stats", 1], ["package-history", 1], ["traffic", 1], ["runs", 1], ["tests", 1]]);
    // SOURCES is what `only=` is validated against; it must name exactly the sources collected.
    expect(logs.map((l) => l[0])).toEqual([...SOURCES]);
  });

  it("names unknown sources, case- and whitespace-exact, and accepts every real one", () => {
    expect(unknownSources(undefined)).toEqual([]);
    expect(unknownSources([...SOURCES])).toEqual([]);
    expect(unknownSources(["tests", "Tests", "test", "code_stats", "Code-Stats", ""])).toEqual(["Tests", "test", "code_stats", "Code-Stats", ""]);
    // The example config lists no eval workflows, so that source does not exist rather than running empty.
    expect(unknownSources(["evals"])).toEqual(["evals"]);
  });

  it("refuses an unknown source before collecting anything", async () => {
    const { db, stmts } = fakeDb();
    const f = async () => {
      throw new Error("must not fetch");
    };
    await expect(collectAll(env(db), { runDays: 1, only: ["nope"], fetcher: f })).rejects.toThrow(/Unknown source\(s\): nope/);
    expect(stmts).toEqual([]);
  });
});

describe("network retries", () => {
  const noPause = async () => {};
  const repo = () => ok({ stargazers_count: 1, forks_count: 2, open_issues_count: 3 });
  const logs = (stmts: Stmt[]) => stmts.filter((s) => s.sql.includes("collect_log")).map((s) => [s.args[1], s.args[2], s.args[3]]);

  it("retries a dropped connection and a gateway error, so a busy night does not fail a source", async () => {
    for (const fail of [() => Promise.reject(new Error("Network connection lost.")), () => Promise.resolve(new Response("", { status: 503 }))]) {
      const { db, stmts } = fakeDb();
      let calls = 0;
      const f = async () => (++calls === 1 ? fail() : repo());
      await collectAll(env(db), { runDays: 1, only: ["repo"], fetcher: f, pause: noPause });
      expect(calls).toBe(2);
      expect(logs(stmts)).toEqual([["repo", 1, "1 stars, 2 forks"]]);
    }
  });

  it("gives up after NETWORK_RETRIES retries with the request named, pausing longer each time", async () => {
    const { db, stmts } = fakeDb();
    const pauses: number[] = [];
    let calls = 0;
    const f = async () => {
      calls++;
      throw new Error("Network connection lost.");
    };
    await expect(collectAll(env(db), { runDays: 1, only: ["repo"], fetcher: f, pause: async (ms) => void pauses.push(ms) })).rejects.toThrow(
      "GET https://api.github.com/repos/example-org/example failed: Network connection lost. Tried 3 times.",
    );
    expect(calls).toBe(1 + NETWORK_RETRIES);
    expect(pauses).toEqual([1000, 2000]);
    expect(logs(stmts)[0]!.slice(0, 2)).toEqual(["repo", 0]);
  });

  it("does not retry an answer that will not change (404, 500), nor a request that is not a GET", async () => {
    for (const status of [404, 500]) {
      let calls = 0;
      const f = withRetries(async () => (calls++, new Response("", { status })), 2, async () => {});
      expect((await f("https://x/")).status).toBe(status);
      expect(calls).toBe(1);
    }
    let posts = 0;
    const post = withRetries(async () => (posts++, new Response("", { status: 503 })), 2, async () => {});
    expect((await post("https://x/", { method: "POST" })).status).toBe(503);
    expect(posts).toBe(1);
  });

  /** Headers arrive, then the connection drops part-way through the body. */
  const cutOff = (first: string) =>
    new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(first)); c.error(new Error("Network connection lost.")); } }), { status: 200 });

  it("retries a connection dropped while the body is being read, which callers read after fetch returns", async () => {
    const { db, stmts } = fakeDb();
    let calls = 0;
    const f = async () => (++calls === 1 ? cutOff('{"stargazers_count":') : repo());
    await collectAll(env(db), { runDays: 1, only: ["repo"], fetcher: f, pause: noPause });
    expect(calls).toBe(2);
    expect(logs(stmts)).toEqual([["repo", 1, "1 stars, 2 forks"]]);
  });

  it("names the request when the body keeps dropping", async () => {
    let calls = 0;
    const f = withRetries(async () => (calls++, cutOff("[")), 2, noPause);
    await expect(f("https://api.github.com/repos/o/r/issues?page=2")).rejects.toThrow(
      "GET https://api.github.com/repos/o/r/issues failed: Network connection lost. Tried 3 times.",
    );
    expect(calls).toBe(3);
  });

  it("hands back the same status, headers and body, and a manual redirect untouched", async () => {
    const f = withRetries(async (u) =>
      u.endsWith("/logs")
        ? new Response(null, { status: 302, headers: { location: "https://blob.example/log" } })
        : new Response("hello", { status: 404, statusText: "Not Found", headers: { link: '<https://x/?page=2>; rel="next"' } }), 2, noPause);
    const res = await f("https://x/");
    expect([res.status, res.statusText, res.headers.get("link"), await res.text()]).toEqual([404, "Not Found", '<https://x/?page=2>; rel="next"', "hello"]);
    const hop = await f("https://x/logs", { redirect: "manual" });
    expect([hop.status, hop.headers.get("location")]).toEqual([302, "https://blob.example/log"]);
  });

  it("is off at 0", async () => {
    let calls = 0;
    const f = withRetries(async () => (calls++, new Response("", { status: 502 })), 0, async () => {});
    expect((await f("https://x/")).status).toBe(502);
    expect(calls).toBe(1);
  });
});

describe("collectRuns with a day that fails", () => {
  it("stores the days it could read, then fails naming the day it could not", async () => {
    const { db, stmts } = fakeDb();
    const f = async (u: string) => {
      const from = decodeURIComponent(new URL(u).searchParams.get("created")!).slice(0, 10);
      if (from === "2026-10-25") return new Response("", { status: 404 });
      return ok({ total_count: 1, workflow_runs: [{ id: Date.parse(from), name: "Unit", head_branch: "main", event: "push", status: "completed", conclusion: "success", head_sha: "a", run_attempt: 1, created_at: `${from}T10:00:00Z`, run_started_at: null, updated_at: `${from}T10:01:00Z` }] });
    };
    await expect(collectRuns(env(db), f, 3, new Date("2026-10-26T12:00:00Z"))).rejects.toThrow(/^2 runs over 2 day\(s\); 1 day\(s\) could not be read: 2026-10-25: GET .* returned HTTP 404/);
    const days = stmts.filter((s) => s.sql.includes("workflow_runs")).map((s) => String(s.args[8]).slice(0, 10));
    expect(days).toEqual(["2026-10-26", "2026-10-24"]);
  });
});

describe("sources that depend on another", () => {
  it("fails tests, rather than reporting 0 days, when runs failed in the same collection", async () => {
    const { db, stmts } = fakeDb();
    const f = async () => new Response("", { status: 404 });
    const err = await collectAll(env(db), { runDays: 1, only: ["runs", "tests"], fetcher: f, pause: async () => {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CollectError);
    expect((err as CollectError).failed).toEqual(["runs", "tests"]);
    const tests = stmts.filter((s) => s.sql.includes("collect_log") && s.args[1] === "tests").map((s) => [s.args[2], s.args[3]]);
    expect(tests).toEqual([[0, "not read: the runs source failed in this collection, and test counts come from its runs."]]);
  });

  it("reads tests normally when runs was not part of the collection", async () => {
    const { db, stmts } = fakeDb();
    await collectAll(env(db), { runDays: 1, only: ["tests"], fetcher: async () => ok({}), pause: async () => {} });
    expect(stmts.filter((s) => s.sql.includes("collect_log")).map((s) => s.args[2])).toEqual([1]);
  });
});

describe("workflowName", () => {
  it("groups GitHub's dynamic workflows, which name each run after its PR or package", async () => {
    const { workflowName } = await import("../src/collector");
    expect(workflowName({ name: "PR #5956", event: "dynamic", path: "dynamic/github-code-scanning/codeql" })).toBe("CodeQL (default setup)");
    expect(workflowName({ name: "npm_and_yarn in /web", event: "dynamic", path: "dynamic/dependabot/dependabot-updates" })).toBe("Dependabot updates");
    expect(workflowName({ name: "Graph Update: pip in /.", event: "dynamic", path: "dynamic/dependabot/update-graph" })).toBe("Dependabot graph update");
    expect(workflowName({ name: "Tests", event: "push", path: ".github/workflows/test_unit.yml" })).toBe("Tests");
    expect(workflowName({ name: null, event: "push" })).toBe("(unnamed)");
  });
});
