import { describe, expect, it, vi } from "vitest";
import { collectEvals, mergeFiles, parseArtifact, parseResultsFile, SCHEMA } from "../src/evals";
import {
  categoryBreakdown,
  compareRuns,
  evalView,
  matchesTrigger,
  parseFilter,
  reliability,
  runKind,
  summarizeRuns,
  type EvalFilter,
  type EvalResultRow,
  type EvalRunRow,
} from "../src/evalstats";
import { listZip, readText } from "../src/zip";
import { memoryD1 } from "./d1";

vi.mock("../src/config", async (orig) => ({ ...(await orig<typeof import("../src/config")>()), EVAL_WORKFLOWS: ["bench.yml", "nightly.yml"] }));

/** Build a real zip (deflate when `deflate`, else stored) so the reader is tested against the actual format. */
async function makeZip(files: Record<string, string>, deflate = false): Promise<Uint8Array> {
  const enc = new TextEncoder();
  const local: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const raw = enc.encode(text);
    const data = deflate
      ? new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer())
      : raw;
    const nm = enc.encode(name);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(8, deflate ? 8 : 0, true);
    lh.setUint32(18, data.length, true);
    lh.setUint32(22, raw.length, true);
    lh.setUint16(26, nm.length, true);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true);
    ch.setUint16(10, deflate ? 8 : 0, true);
    ch.setUint32(20, data.length, true);
    ch.setUint32(24, raw.length, true);
    ch.setUint16(28, nm.length, true);
    ch.setUint32(42, offset, true);
    local.push(new Uint8Array(lh.buffer), nm, data);
    central.push(new Uint8Array(ch.buffer), nm);
    offset += 30 + nm.length + data.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, Object.keys(files).length, true);
  eocd.setUint16(10, Object.keys(files).length, true);
  eocd.setUint32(12, cdSize, true);
  eocd.setUint32(16, offset, true);
  const parts = [...local, ...central, new Uint8Array(eocd.buffer)];
  const out = new Uint8Array(parts.reduce((a, b) => a + b.length, 0));
  let p = 0;
  for (const x of parts) {
    out.set(x, p);
    p += x.length;
  }
  return out;
}

const file = (over: Record<string, unknown> = {}, results: Record<string, unknown>[] = [{ id: "login_flow", status: "PASS" }]) =>
  JSON.stringify({ schema: SCHEMA, suite: "agent-scenarios", tier: "smoke", model: "m1", results, ...over });

describe("zip reader", () => {
  it("reads deflated and stored entries", async () => {
    for (const deflate of [true, false]) {
      const z = await makeZip({ "a/eval-results.json": '{"x":1}', "b.txt": "hello" }, deflate);
      const entries = listZip(z);
      expect(entries.map((e) => e.name)).toEqual(["a/eval-results.json", "b.txt"]);
      expect(await readText(z, entries[1]!)).toBe("hello");
    }
  });
});

describe("parseResultsFile", () => {
  it("maps a valid file, upper-cases the status and leaves absent fields null", () => {
    const rows = parseResultsFile(
      file({}, [
        { id: "login_flow", category: "auth", status: "pass", score: 0.92, elapsed_s: 31.2, cost_usd: 0.004, input_tokens: 1200, output_tokens: 300, note: "ok" },
        { id: "logout", status: "Timeout" },
      ]),
      "eval-results/eval-results.json",
    );
    expect(rows[0]).toEqual({
      suite: "agent-scenarios", tier: "smoke", model: "m1", category: "auth", id: "login_flow", status: "PASS",
      score: 0.92, elapsed_s: 31.2, cost_usd: 0.004, input_tokens: 1200, output_tokens: 300, note: "ok",
    });
    expect(rows[1]).toMatchObject({ status: "TIMEOUT", category: null, score: null, elapsed_s: null, cost_usd: null, note: null });
  });

  it("accepts a file with only the required fields", () => {
    const [r] = parseResultsFile(JSON.stringify({ schema: SCHEMA, suite: "s", results: [{ id: "a", status: "FAIL" }] }), "f.json");
    expect(r).toMatchObject({ suite: "s", tier: null, model: null, status: "FAIL" });
  });

  it("rejects a wrong or missing schema, naming the file and the expected value", () => {
    expect(() => parseResultsFile(file({ schema: "oss-dashboard/eval-results@2" }), "art/x/eval-results.json")).toThrow(
      /art\/x\/eval-results\.json has schema "oss-dashboard\/eval-results@2"; expected "oss-dashboard\/eval-results@1"/,
    );
    expect(() => parseResultsFile(JSON.stringify({ suite: "s", results: [] }), "f.json")).toThrow(/f\.json has schema null/);
  });

  it("rejects a result without an id, or without a suite", () => {
    expect(() => parseResultsFile(file({}, [{ status: "PASS" }]), "f.json")).toThrow(/f\.json: results\[0\]\."id" is required/);
    expect(() => parseResultsFile(file({ suite: "" }), "f.json")).toThrow(/"suite" must not be empty/);
    expect(() => parseResultsFile(file({ suite: undefined }), "f.json")).toThrow(/"suite" is required/);
  });

  it("rejects an unknown status, naming the file, the result and the value", () => {
    expect(() => parseResultsFile(file({}, [{ id: "a", status: "FLAKY" }]), "f.json")).toThrow(
      /f\.json: result "a" has status "FLAKY"; expected one of PASS, FAIL, TIMEOUT, ERROR, SKIPPED/,
    );
  });

  it("rejects a present field of the wrong type instead of dropping it", () => {
    expect(() => parseResultsFile(file({}, [{ id: "a", status: "PASS", score: "0.9" }]), "f.json")).toThrow(/result "a" "score" must be a number, got "0.9"/);
    expect(() => parseResultsFile(file({}, [{ id: 7, status: "PASS" }]), "f.json")).toThrow(/"id" must be a string, got 7/);
  });

  it("rejects the same result twice in one file, but allows one id in two categories", () => {
    expect(() => parseResultsFile(file({}, [{ id: "a", status: "PASS" }, { id: "a", status: "FAIL" }]), "f.json")).toThrow(/result "a" appears more than once/);
    expect(parseResultsFile(file({}, [{ id: "a", category: "x", status: "PASS" }, { id: "a", category: "y", status: "PASS" }]), "f.json")).toHaveLength(2);
  });

  it("names the file when it is not JSON", () => {
    expect(() => parseResultsFile("{broken", "f.json")).toThrow(/f\.json is not valid JSON/);
  });
});

describe("parseArtifact and mergeFiles", () => {
  it("reads every eval-results.json in any directory and ignores other files", async () => {
    const z = await makeZip({
      "eval-results.json": file({ suite: "a" }),
      "shard-2/out/eval-results.json": file({ suite: "b" }),
      "shard-2/out/trace.json": "{not json and not read}",
    });
    const files = await parseArtifact(z, "eval-results-linux");
    expect(files.map((f) => [f.file, f.rows[0]!.suite])).toEqual([
      ["eval-results-linux/eval-results.json", "a"],
      ["eval-results-linux/shard-2/out/eval-results.json", "b"],
    ]);
  });

  it("fails on an eval-results artifact that holds no eval-results.json", async () => {
    await expect(parseArtifact(await makeZip({ "results.json": "{}" }), "eval-results")).rejects.toThrow(/Artifact "eval-results" contains no eval-results\.json/);
  });

  it("fails when two files report the same result for the same suite, tier and model; other models are separate", () => {
    const rows = (model: string) => parseResultsFile(file({ model }), "x");
    expect(() => mergeFiles([{ file: "a.json", rows: rows("m1") }, { file: "b.json", rows: rows("m1") }])).toThrow(/appears in both a\.json and b\.json/);
    expect(mergeFiles([{ file: "a.json", rows: rows("m1") }, { file: "b.json", rows: rows("m2") }])).toHaveLength(2);
  });
});

const run = (id: number, at: string, over: Partial<EvalRunRow> = {}): EvalRunRow => ({
  run_id: id, workflow: "Bench", event: "schedule", ref: "main", sha: "abc" + id, created_at: at, conclusion: "success", html_url: "u", ...over,
});
const row = (runId: number, id: string, status: string, over: Partial<EvalResultRow> = {}): EvalResultRow => ({
  run_id: runId, suite: "s", tier: "smoke", model: "m1", category: "cat", result_id: id, status, score: null, elapsed_s: 10, cost_usd: null,
  input_tokens: null, output_tokens: null, note: null, ...over,
});
const day = (d: number) => `2026-10-${String(d).padStart(2, "0")}T00:00:00Z`;
const filter = (over: Partial<EvalFilter> = {}): EvalFilter => ({ suite: "s", trig: "all", tier: "", model: "", category: "", ...over });

describe("pass rate", () => {
  it("counts TIMEOUT as a failure and leaves ERROR and SKIPPED out of the denominator", () => {
    const [s] = summarizeRuns([run(1, day(1))], [row(1, "a", "PASS"), row(1, "b", "FAIL"), row(1, "c", "TIMEOUT"), row(1, "d", "ERROR"), row(1, "e", "SKIPPED")]);
    expect(s).toMatchObject({ results: 5, passed: 1, failed: 1, timeout: 1, errors: 1, skipped: 1 });
    expect(s!.passRate).toBeCloseTo(1 / 3);
  });

  it("has no pass rate for a run that only errored, but still lists the run", () => {
    const [s] = summarizeRuns([run(1, day(1))], [row(1, "a", "ERROR")]);
    expect(s).toMatchObject({ passRate: null, errors: 1 });
  });

  it("leaves cancelled runs out and orders by time", () => {
    const out = summarizeRuns([run(2, day(2)), run(1, day(1)), run(3, day(3), { conclusion: "cancelled" })], [row(2, "a", "PASS"), row(1, "a", "FAIL"), row(3, "a", "PASS")]);
    expect(out.map((r) => r.run_id)).toEqual([1, 2]);
  });

  it("averages cost over the results that report one", () => {
    const [s] = summarizeRuns([run(1, day(1))], [row(1, "a", "PASS", { cost_usd: 0.4 }), row(1, "b", "PASS", { cost_usd: null })]);
    expect(s).toMatchObject({ costUsd: 0.4, costPerResult: 0.4 });
  });

  it("splits one GitHub run into one run per model, with stable keys", () => {
    const out = summarizeRuns([run(1, day(1))], [row(1, "a", "PASS", { model: "m2" }), row(1, "a", "FAIL", { model: "m1" })]);
    expect(out.map((r) => [r.key, r.model, r.passRate])).toEqual([["1.0", "m1", 0], ["1.1", "m2", 1]]);
  });
});

describe("regressions", () => {
  it("compares each result with the previous run of the same series", () => {
    const runs = summarizeRuns([run(1, day(1)), run(2, day(2))], [row(1, "a", "PASS"), row(1, "b", "FAIL"), row(1, "c", "PASS"), row(2, "a", "FAIL"), row(2, "b", "PASS"), row(2, "c", "TIMEOUT")]);
    const { runs: out, previous } = compareRuns(runs, [row(1, "a", "PASS"), row(1, "b", "FAIL"), row(1, "c", "PASS"), row(2, "a", "FAIL"), row(2, "b", "PASS"), row(2, "c", "TIMEOUT")]);
    expect(out.map((r) => [r.regressed, r.fixed])).toEqual([[null, null], [2, 1]]);
    expect(previous.get("2.0")!.get("cat\u0000a")).toEqual({ status: "PASS", key: "1.0", run_id: 1 });
  });

  it("never compares across models or tiers", () => {
    const rows = [row(1, "a", "PASS", { model: "m1" }), row(2, "a", "FAIL", { model: "m2" }), row(3, "a", "FAIL", { tier: "full" })];
    const { runs } = compareRuns(summarizeRuns([run(1, day(1)), run(2, day(2)), run(3, day(3))], rows), rows);
    expect(runs.map((r) => [r.run_id, r.regressed])).toEqual([[1, null], [2, null], [3, null]]);
  });

  it("looks past an ERROR to the last pass-or-fail outcome, and an ERROR is not a regression", () => {
    const rows = [row(1, "a", "PASS"), row(2, "a", "ERROR"), row(3, "a", "FAIL")];
    const { runs } = compareRuns(summarizeRuns([run(1, day(1)), run(2, day(2)), run(3, day(3))], rows), rows);
    expect(runs.map((r) => r.regressed)).toEqual([null, 0, 1]);
  });

  it("is reported for the selected run with the status it had before", () => {
    const runs = [run(1, day(1)), run(2, day(2)), run(3, day(3), { event: "pull_request", ref: "feature" })];
    const rows = [row(1, "a", "PASS"), row(2, "a", "FAIL"), row(3, "a", "PASS")];
    const v = evalView(runs, rows, filter({ trig: "main" }), day(1), null);
    expect(v.runs.map((r) => r.run_id)).toEqual([1, 2]);
    expect(v.selected).toBe("2.0");
    expect(v.results).toEqual([expect.objectContaining({ result_id: "a", status: "FAIL", previous: "PASS", previousRun: 1 })]);
  });

  it("compares the first run in range with an earlier one outside it", () => {
    const v = evalView([run(1, day(1)), run(2, day(5))], [row(1, "a", "PASS"), row(2, "a", "FAIL")], filter(), day(3), null);
    expect(v.runs.map((r) => [r.run_id, r.regressed])).toEqual([[2, 1]]);
  });
});

describe("triggers", () => {
  it("detects releases and release candidates from the tag, whatever event started the run", () => {
    expect(runKind({ event: "push", ref: "v1.2.0" })).toBe("Release");
    expect(runKind({ event: "push", ref: "v1.2.0-rc1" })).toBe("Release candidate");
    expect(runKind({ event: "workflow_dispatch", ref: "1.2.0-beta.2" })).toBe("Release candidate");
    expect(runKind({ event: "release", ref: "v2.0.0" })).toBe("Release");
    expect(runKind({ event: "workflow_dispatch", ref: "main" })).toBe("Manual");
    expect(runKind({ event: "schedule", ref: "main" })).toBe("Scheduled");
    expect(runKind({ event: "pull_request", ref: "v9.9.9" })).toBe("Pull request");
  });

  it("counts default-branch pushes and schedules plus releases as `main`, not feature branches", () => {
    expect(matchesTrigger({ event: "push", ref: "main" }, "main")).toBe(true);
    expect(matchesTrigger({ event: "push", ref: "v1.0.0" }, "main")).toBe(true);
    expect(matchesTrigger({ event: "push", ref: "feature" }, "main")).toBe(false);
    expect(matchesTrigger({ event: "push", ref: "v1.0.0-rc2" }, "release")).toBe(false);
    expect(matchesTrigger({ event: "push", ref: "v1.0.0-rc2" }, "rc")).toBe(true);
  });

  it("parses the filter, defaulting the suite to the first one offered", () => {
    expect(parseFilter(new URLSearchParams("trig=bogus&tier=full"), ["x", "y"])).toEqual({ suite: "x", trig: "main", tier: "full", model: "", category: "" });
    expect(parseFilter(new URLSearchParams("suite=y&trig=rc"), ["x", "y"])).toMatchObject({ suite: "y", trig: "rc" });
  });
});

describe("evalView", () => {
  const runs = [run(1, day(1)), run(2, day(2))];
  const rows = [
    row(1, "a", "PASS", { suite: "s", category: "x" }), row(1, "b", "FAIL", { suite: "s", category: "y" }),
    row(1, "q", "FAIL", { suite: "t", tier: "", model: "", category: "" }),
    row(2, "a", "FAIL", { suite: "s", category: "x" }), row(2, "b", "PASS", { suite: "s", category: "y" }),
  ];

  it("keeps suites of a multi-suite run apart", () => {
    const s = evalView(runs, rows, filter({ suite: "s" }), day(1), null);
    const t = evalView(runs, rows, filter({ suite: "t" }), day(1), null);
    expect(s.runs.map((r) => [r.run_id, r.results])).toEqual([[1, 2], [2, 2]]);
    expect(t.runs.map((r) => [r.run_id, r.results, r.passRate])).toEqual([[1, 1, 0]]);
    expect(t.options).toEqual({ tiers: [], models: [], categories: [] });
  });

  it("narrows to a category without renumbering runs, and offers every category", () => {
    const v = evalView(runs, rows, filter({ category: "x" }), day(1), "1.0");
    expect(v.runs.map((r) => [r.key, r.results])).toEqual([["1.0", 1], ["2.0", 1]]);
    expect(v.selected).toBe("1.0");
    expect(v.options.categories).toEqual(["x", "y"]);
  });

  it("falls back to the newest run when the requested one is not shown", () => {
    expect(evalView(runs, rows, filter(), day(1), "99.0").selected).toBe("2.0");
  });

  it("breaks the selected run down by category against the previous comparable run", () => {
    const v = evalView(runs, rows, filter(), day(1), null);
    expect(v.byCategory.map((c) => [c.category, c.passRate, c.prevPassRate])).toEqual([["x", 0, 1], ["y", 1, 0]]);
  });
});

describe("categoryBreakdown and reliability", () => {
  it("skips earlier runs that did not measure a category", () => {
    const [c] = categoryBreakdown([row(3, "a", "PASS", { category: "x" })], [[row(2, "z", "PASS", { category: "y" })], [row(1, "a", "FAIL", { category: "x" })]]);
    expect(c).toMatchObject({ category: "x", passRate: 1, prevPassRate: 0 });
  });

  it("ranks the least reliable results first, marks flaky ones, and does not count errors as failures", () => {
    const rows = [row(1, "a", "PASS"), row(2, "a", "FAIL"), row(1, "b", "FAIL"), row(2, "b", "TIMEOUT"), row(1, "c", "PASS"), row(2, "c", "ERROR")];
    const ms = summarizeRuns([run(1, day(1)), run(2, day(2))], rows);
    expect(reliability(ms, rows).map((r) => [r.result_id, r.passRate, r.flaky, r.undecided, r.lastStatus])).toEqual([
      ["b", 0, false, 0, "TIMEOUT"],
      ["a", 0.5, true, 0, "FAIL"],
      ["c", 1, false, 1, "ERROR"],
    ]);
  });
});

describe("collectEvals against real SQLite", () => {
  const env = (db: D1Database) => ({ DB: db });
  const NOW = new Date();
  const ghRun = (id: number, attempt = 1, over: Record<string, unknown> = {}) => ({
    id, event: "schedule", head_branch: "main", head_sha: "abc" + id, created_at: NOW.toISOString(), conclusion: "success", html_url: "u", name: "Bench", run_attempt: attempt, ...over,
  });
  type Art = { name: string; zip: Uint8Array | "gone"; size?: number };
  function github(runs: Record<string, ReturnType<typeof ghRun>[]>, arts: Record<number, Art[]>, calls: string[] = []) {
    const byId = new Map<number, Art>();
    let next = 1000;
    const listed = Object.fromEntries(
      Object.entries(arts).map(([run, list]) => [
        run,
        list.map((a) => {
          const id = next++;
          byId.set(id, a);
          return { id, name: a.name, size_in_bytes: a.size ?? 100, expired: a.zip === "gone" };
        }),
      ]),
    );
    return async (url: string) => {
      calls.push(url);
      const wf = url.match(/\/workflows\/([^/]+)\/runs/);
      if (wf) return new Response(JSON.stringify({ workflow_runs: runs[decodeURIComponent(wf[1]!)] ?? [] }));
      const art = url.match(/runs\/(\d+)\/artifacts/);
      if (art) return new Response(JSON.stringify({ artifacts: listed[art[1]!] ?? [] }));
      const dl = url.match(/artifacts\/(\d+)\/zip/);
      if (dl) return new Response(byId.get(Number(dl[1]))!.zip as Uint8Array);
      return new Response("not found", { status: 404 });
    };
  }
  const results = (status: string, over: Record<string, unknown> = {}) => makeZip({ "out/eval-results.json": file(over, [{ id: "login_flow", category: "auth", status }]) });
  const stored = async (db: D1Database) =>
    (await db.prepare("SELECT s.run_id, s.suite, s.status, r.run_attempt FROM eval_results s JOIN eval_runs r USING(run_id) ORDER BY s.run_id, s.suite").all()).results;

  it("stores every suite of a run, from every eval-results artifact, and never downloads other artifacts", async () => {
    const db = memoryD1();
    const calls: string[] = [];
    const arts = { 1: [{ name: "eval-results-a", zip: await results("PASS", { suite: "a" }) }, { name: "eval-results-b", zip: await results("fail", { suite: "b" }) }, { name: "build-output", zip: "gone" as const, size: 9e9 }] };
    expect(await collectEvals(env(db), github({ "bench.yml": [ghRun(1)] }, arts, calls), 14, NOW)).toBe("1 eval runs, 2 results");
    expect(await stored(db)).toEqual([{ run_id: 1, suite: "a", status: "PASS", run_attempt: 1 }, { run_id: 1, suite: "b", status: "FAIL", run_attempt: 1 }]);
    expect(calls.filter((c) => c.endsWith("/zip"))).toHaveLength(2);
  });

  it("re-reads a run when GitHub reports a newer attempt, and skips it otherwise", async () => {
    const db = memoryD1();
    const calls: string[] = [];
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1, 1)] }, { 1: [{ name: "eval-results", zip: await results("FAIL") }] }), 14, NOW);
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1, 1)] }, { 1: [{ name: "eval-results", zip: await results("FAIL") }] }, calls), 14, NOW);
    expect(calls.some((c) => c.includes("/artifacts"))).toBe(false);
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1, 2)] }, { 1: [{ name: "eval-results", zip: await results("PASS") }] }), 14, NOW);
    expect(await stored(db)).toEqual([{ run_id: 1, suite: "agent-scenarios", status: "PASS", run_attempt: 2 }]);
  });

  it("keeps a stored run's results after its artifacts expire, without reporting a failure", async () => {
    const db = memoryD1();
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1)] }, { 1: [{ name: "eval-results", zip: await results("PASS") }] }), 14, NOW);
    const summary = await collectEvals(env(db), github({ "bench.yml": [ghRun(1)] }, { 1: [{ name: "eval-results", zip: "gone" }] }), 14, NOW, true);
    expect(summary).toMatch(/1 stored runs kept as stored/);
    expect(await stored(db)).toEqual([{ run_id: 1, suite: "agent-scenarios", status: "PASS", run_attempt: 1 }]);
  });

  it("reports a new run whose artifacts expired, and a newer attempt it cannot read keeps the old data", async () => {
    const db = memoryD1();
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1, 1)] }, { 1: [{ name: "eval-results", zip: await results("FAIL") }] }), 14, NOW);
    await expect(
      collectEvals(env(db), github({ "bench.yml": [ghRun(1, 2), ghRun(2)] }, { 1: [{ name: "eval-results", zip: "gone" }], 2: [{ name: "eval-results", zip: "gone" }] }), 14, NOW),
    ).rejects.toThrow(/2 run\(s\) could not be read: .*expired/);
    expect(await stored(db)).toEqual([{ run_id: 1, suite: "agent-scenarios", status: "FAIL", run_attempt: 1 }]);
  });

  it("stores nothing of a run with an invalid file and names the file in the error, while other runs are stored", async () => {
    const db = memoryD1();
    const bad = await makeZip({ "eval-results.json": file({}, [{ id: "a", status: "FLAKY" }]) });
    const gh = github({ "bench.yml": [ghRun(1)], "nightly.yml": [ghRun(2)] }, { 1: [{ name: "eval-results", zip: bad }], 2: [{ name: "eval-results", zip: await results("PASS") }] });
    await expect(collectEvals(env(db), gh, 14, NOW)).rejects.toThrow(/Run 1: eval-results\/eval-results\.json: result "a" has status "FLAKY"/);
    expect((await stored(db)).map((r) => (r as { run_id: number }).run_id)).toEqual([2]);
    expect(await db.prepare("SELECT COUNT(*) AS n FROM eval_runs WHERE run_id = 1").first()).toEqual({ n: 0 });
  });

  it("remembers a run with no eval-results artifact so it is not fetched again", async () => {
    const db = memoryD1();
    const calls: string[] = [];
    expect(await collectEvals(env(db), github({ "bench.yml": [ghRun(3)] }, { 3: [{ name: "logs", zip: "gone" }] }), 14, NOW)).toMatch(/1 finished runs had no eval-results\* artifact/);
    await collectEvals(env(db), github({ "bench.yml": [ghRun(3)] }, {}, calls), 14, NOW);
    expect(calls.some((c) => c.includes("/runs/3/artifacts"))).toBe(false);
    expect(await db.prepare("SELECT files FROM eval_runs WHERE run_id = 3").first()).toEqual({ files: 0 });
  });

  it("reads at most 40 runs per collection, newest first across workflows", async () => {
    const db = memoryD1();
    const at = (d: number) => ({ created_at: new Date(NOW.getTime() - d * 86_400_000).toISOString() });
    const old = Array.from({ length: 40 }, (_, i) => ghRun(100 + i, 1, at(12)));
    const arts: Record<number, Art[]> = Object.fromEntries(old.map((r) => [r.id, [{ name: "eval-results", zip: "gone" as const }]]));
    arts[7] = [{ name: "eval-results", zip: await results("PASS") }];
    const calls: string[] = [];
    await expect(collectEvals(env(db), github({ "bench.yml": old, "nightly.yml": [ghRun(7, 1, at(1))] }, arts, calls), 14, NOW)).rejects.toThrow(/1 more runs left for the next collection/);
    expect(calls.filter((c) => /runs\/\d+\/artifacts/.test(c))).toHaveLength(40);
    expect((await db.prepare("SELECT run_id FROM eval_runs").all()).results).toEqual([{ run_id: 7 }]);
  });

  it("works through every stored run over repeated full refreshes, oldest-read first", async () => {
    const db = memoryD1();
    const runs = Array.from({ length: 45 }, (_, i) => ghRun(i + 1));
    const zip = await results("PASS");
    const gh = github({ "bench.yml": runs }, Object.fromEntries(runs.map((r) => [r.id, [{ name: "eval-results", zip }]])));
    await collectEvals(env(db), gh, 14, NOW);
    await collectEvals(env(db), gh, 14, NOW);
    const before = new Map((await db.prepare("SELECT run_id, collected_at FROM eval_runs").all<{ run_id: number; collected_at: string }>()).results.map((r) => [r.run_id, r.collected_at]));
    expect(before.size).toBe(45);
    await new Promise((r) => setTimeout(r, 5));
    await collectEvals(env(db), gh, 14, NOW, true);
    await new Promise((r) => setTimeout(r, 5));
    await collectEvals(env(db), gh, 14, NOW, true);
    const after = (await db.prepare("SELECT run_id, collected_at FROM eval_runs").all<{ run_id: number; collected_at: string }>()).results;
    expect(after.filter((r) => r.collected_at === before.get(r.run_id))).toEqual([]);
  });

  it("stores the tag of a release run so the page can tell releases apart", async () => {
    const db = memoryD1();
    await collectEvals(env(db), github({ "bench.yml": [ghRun(1, 1, { event: "push", head_branch: "v1.4.0-rc2" })] }, { 1: [{ name: "eval-results", zip: await results("PASS") }] }), 14, NOW);
    const r = await db.prepare("SELECT event, ref FROM eval_runs").first<{ event: string; ref: string }>();
    expect(runKind(r!)).toBe("Release candidate");
  });
});
