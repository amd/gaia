// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { parseAgentScorecard, parseArtifact, parseTasksScorecard } from "../src/evals";
import {
  categoryBreakdown,
  filterRuns,
  parseFilter,
  runKind,
  scenarioDetails,
  scenarioReliability,
  summarizeRuns,
  type EvalRunRow,
  type EvalScenarioRow,
} from "../src/evalstats";
import { listZip, readText } from "../src/zip";

/** Build a real zip (deflate when `deflate`, else stored) so the reader is tested against the actual format. */
async function makeZip(files: Record<string, string>, deflate = true): Promise<Uint8Array> {
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

const scenario = (over: Record<string, unknown> = {}) => ({
  scenario_id: "files_sandbox_refusal_system",
  status: "PASS",
  overall_score: 8.95,
  elapsed_s: 195.9,
  category: "gaia_files",
  cost_estimate: { turns: 3, estimated_usd: 0.23 },
  turns: [{}, {}, {}],
  root_cause: null,
  performance_summary: { avg_tokens_per_second: 41.6, avg_time_to_first_token: 2.345, total_input_tokens: 24349, total_output_tokens: 895, total_tool_calls: 5 },
  tool_usage: { total_tool_calls: 5 },
  ...over,
});

describe("zip reader", () => {
  it("reads deflated and stored entries", async () => {
    for (const deflate of [true, false]) {
      const z = await makeZip({ "a/scorecard.json": '{"x":1}', "b.txt": "hello" }, deflate);
      const entries = listZip(z);
      expect(entries.map((e) => e.name)).toEqual(["a/scorecard.json", "b.txt"]);
      expect(await readText(z, entries[1]!)).toBe("hello");
    }
  });

  it("rejects something that is not a zip", () => {
    expect(() => listZip(new Uint8Array(100))).toThrow(/Not a zip/);
  });
});

describe("parseAgentScorecard", () => {
  it("maps a real scorecard record", () => {
    const [r] = parseAgentScorecard({ scenarios: [scenario()] }, "gaia_files", "smoke-1");
    expect(r).toEqual({
      category: "gaia_files", scenario_id: "files_sandbox_refusal_system", lane: "smoke-1", status: "PASS", score: 8.95,
      elapsed_s: 195.9, cost_usd: 0.23, tps: 41.6, ttft_s: 2.345, input_tokens: 24349, output_tokens: 895, tool_calls: 5,
      turns: 3, root_cause: null, kind: "scenario",
    });
  });

  it("treats a literal \"null\" root cause as no root cause", () => {
    const [r] = parseAgentScorecard({ scenarios: [scenario({ root_cause: "null" })] }, "gaia_files", null);
    expect(r!.root_cause).toBeNull();
  });

  it("falls back to the directory for the category and caps long root causes", () => {
    const [r] = parseAgentScorecard({ scenarios: [scenario({ category: undefined, root_cause: "x".repeat(500) })] }, "dir_cat", null);
    expect(r!.category).toBe("dir_cat");
    expect(r!.root_cause!.length).toBe(300);
  });
});

describe("parseTasksScorecard", () => {
  it("counts a model backend that never answered as an infrastructure error, not a failure", () => {
    const rows = parseTasksScorecard({
      suite: "swebench",
      tasks: [
        { id: "a", passed: false, error: "model backend failed: The selected cloud model is unavailable (HTTP 404).", output_tokens: 0, wall_seconds: 4, steps: 1 },
        { id: "b", passed: false, error: null, output_tokens: 900, wall_seconds: 400 },
        { id: "c", passed: true, wall_seconds: 300, cost_usd: 0.12 },
        { id: "d", passed: false, timed_out: true },
      ],
    });
    expect(rows.map((r) => [r.scenario_id, r.status])).toEqual([["a", "INFRA_ERROR"], ["b", "FAIL"], ["c", "PASS"], ["d", "TIMEOUT"]]);
    expect(rows[0]!.category).toBe("swebench");
  });
});

describe("parseArtifact", () => {
  it("reads lane, tier, models and every category's scorecard from an artifact", async () => {
    const z = await makeZip({
      "lane-status.json": JSON.stringify({ lane: "smoke-1", tier: "smoke", agent_model: "Qwen3.6-35B-A3B-GGUF" }),
      "lane-timing.json": JSON.stringify({ lane: "smoke-1", total_minutes: 29.7 }),
      "gaia_files/scorecard.json": JSON.stringify({ config: { model: "claude-sonnet-4-6" }, scenarios: [scenario()] }),
      "gaia_files/traces/x.json": "{not json and not read}",
      "gaia_web/scorecard.json": JSON.stringify({ scenarios: [scenario({ scenario_id: "web_fetch", category: "gaia_web", status: "FAIL" })] }),
    });
    const p = await parseArtifact(z);
    expect(p).toMatchObject({ tier: "smoke", agentModel: "Qwen3.6-35B-A3B-GGUF", judgeModel: "claude-sonnet-4-6", laneMinutes: 29.7 });
    expect(p.scenarios.map((s) => [s.category, s.status, s.lane])).toEqual([["gaia_files", "PASS", "smoke-1"], ["gaia_web", "FAIL", "smoke-1"]]);
  });

  it("takes the release tag from tier.json", async () => {
    const z = await makeZip({ "tier.json": JSON.stringify({ tier: "smoke", ref: "refs/tags/v0.25.2-rc1", agent_model: "Q" }) });
    expect(await parseArtifact(z)).toMatchObject({ ref: "refs/tags/v0.25.2-rc1", tier: "smoke", scenarios: [] });
  });

  it("names the artifact problem when a scorecard is not valid JSON", async () => {
    const z = await makeZip({ "cat/scorecard.json": "{broken" });
    await expect(parseArtifact(z)).rejects.toThrow();
  });
});

const run = (id: number, at: string, over: Partial<EvalRunRow> = {}): EvalRunRow => ({
  run_id: id, workflow: "Agent Eval — flagship", event: "schedule", ref: "main", is_tag: 0, sha: "abc" + id, created_at: at,
  conclusion: "success", tier: "full", agent_model: "Q", judge_model: "claude", minutes: 30, html_url: "u", ...over,
});
const row = (runId: number, sid: string, status: string, over: Partial<EvalScenarioRow> = {}): EvalScenarioRow => ({
  run_id: runId, category: "cat", scenario_id: sid, status, score: status === "PASS" ? 9 : 3, elapsed_s: 100, cost_usd: 0.1,
  tps: 40, ttft_s: 2, input_tokens: 10, output_tokens: 5, tool_calls: 2, turns: 3, root_cause: null, kind: "scenario", ...over,
});

describe("runKind", () => {
  it("names triggers", () => {
    expect(runKind({ event: "push", is_tag: 1, ref: "v0.25.2-rc1", tier: "smoke" })).toBe("Release candidate");
    expect(runKind({ event: "push", is_tag: 1, ref: "v0.25.1", tier: null })).toBe("Release");
    expect(runKind({ event: "schedule", is_tag: 0, ref: "main", tier: "full" })).toBe("Nightly");
    expect(runKind({ event: "pull_request", is_tag: 0, ref: "x", tier: null })).toBe("Pull request");
  });
});

describe("summarizeRuns", () => {
  it("computes pass rate over everything attempted and judged pass rate without infra noise", () => {
    const [s] = summarizeRuns(
      [run(1, "2026-10-01T00:00:00Z")],
      [row(1, "a", "PASS"), row(1, "b", "FAIL"), row(1, "c", "TIMEOUT"), row(1, "d", "INFRA_ERROR")],
    );
    expect(s).toMatchObject({ scenarios: 4, passed: 1, failed: 1, timeout: 1, infra: 1, passRate: 0.25, judgedPassRate: 0.5, kind: "Nightly" });
    expect(s!.costUsd).toBeCloseTo(0.4);
  });

  it("leaves skipped scenarios out of the pass rate and infers the tier of older runs", () => {
    const rows = [row(1, "a", "PASS"), row(1, "b", "SKIPPED_NO_MODEL")];
    const [s] = summarizeRuns([run(1, "2026-10-01T00:00:00Z", { tier: null })], rows);
    expect(s).toMatchObject({ passRate: 1, skipped: 1, other: 0, tierLabel: "partial" });
  });

  it("keeps task-suite results out of a scenario run's pass rate (the v0.25.1-rc2 case)", () => {
    const rows = [row(1, "a", "PASS"), row(1, "b", "FAIL"), row(1, "t1", "PASS", { kind: "task", category: "everyday" }), row(1, "t2", "PASS", { kind: "task", category: "therock" })];
    const [s] = summarizeRuns([run(1, "2026-10-01T00:00:00Z")], rows);
    expect(s).toMatchObject({ scenarios: 2, passed: 1, passRate: 0.5, measures: "scenarios", tasks: 2, tasksPassed: 2 });
  });

  it("measures a task-only run (SWE-bench) by its tasks", () => {
    const [s] = summarizeRuns([run(1, "2026-10-01T00:00:00Z")], [row(1, "t1", "FAIL", { kind: "task" }), row(1, "t2", "INFRA_ERROR", { kind: "task" })]);
    expect(s).toMatchObject({ scenarios: 2, measures: "tasks", tasks: 0, infra: 1 });
  });

  it("drops runs with no scenarios and orders by time", () => {
    const out = summarizeRuns([run(2, "2026-10-02T00:00:00Z"), run(1, "2026-10-01T00:00:00Z"), run(3, "2026-10-03T00:00:00Z")], [row(2, "a", "PASS"), row(1, "a", "FAIL")]);
    expect(out.map((r) => r.run_id)).toEqual([1, 2]);
  });
});

describe("scenarioDetails", () => {
  it("shows each scenario's status in the latest earlier comparable run that ran it", () => {
    const earlier = [run(1, "2026-10-01T00:00:00Z"), run(2, "2026-10-02T00:00:00Z")];
    const earlierRows = [row(1, "a", "PASS"), row(2, "a", "FAIL"), row(1, "b", "PASS")];
    const sel = [row(3, "a", "PASS"), row(3, "new", "FAIL"), row(3, "b", "FAIL")];
    expect(scenarioDetails(sel, earlier, earlierRows).map((x) => [x.scenario_id, x.status, x.previous, x.previousRun])).toEqual([
      ["a", "PASS", "FAIL", 2],
      ["b", "FAIL", "PASS", 1],
      ["new", "FAIL", null, null],
    ]);
  });

  it("ignores rows from runs the caller did not pass as comparable", () => {
    const d = scenarioDetails([row(3, "a", "FAIL")], [run(1, "2026-10-01T00:00:00Z")], [row(1, "a", "PASS"), row(2, "a", "FAIL")]);
    expect(d[0]!.previousRun).toBe(1);
  });
});

describe("scenarioReliability", () => {
  it("ranks the least reliable scenarios first and marks flaky ones", () => {
    const runs = [run(1, "2026-10-01T00:00:00Z"), run(2, "2026-10-02T00:00:00Z")];
    const out = scenarioReliability(runs, [row(1, "a", "PASS"), row(2, "a", "FAIL"), row(1, "b", "FAIL"), row(2, "b", "FAIL"), row(1, "c", "PASS"), row(2, "c", "PASS")]);
    expect(out.map((r) => [r.scenario_id, r.passRate, r.flaky, r.lastStatus])).toEqual([["b", 0, false, "FAIL"], ["a", 0.5, true, "FAIL"], ["c", 1, false, "PASS"]]);
  });

  it("does not count infrastructure errors as failures or as flakiness", () => {
    const runs = [run(1, "2026-10-01T00:00:00Z"), run(2, "2026-10-02T00:00:00Z")];
    const [r] = scenarioReliability(runs, [row(1, "a", "PASS"), row(2, "a", "INFRA_ERROR")]);
    expect(r).toMatchObject({ passRate: 1, flaky: false, failed: 0, notJudged: 1, lastStatus: "INFRA_ERROR" });
  });
});

describe("categoryBreakdown", () => {
  it("compares each category with the previous run that measured it", () => {
    const earlier = [run(1, "2026-10-01T00:00:00Z"), run(2, "2026-10-02T00:00:00Z")];
    const earlierRows = [row(1, "a", "PASS", { category: "x" }), row(2, "b", "FAIL", { category: "y" })];
    const sel = [row(3, "a", "FAIL", { category: "x" }), row(3, "b", "PASS", { category: "y" })];
    expect(categoryBreakdown(sel, earlier, earlierRows).map((c) => [c.category, c.passRate, c.prevPassRate])).toEqual([["x", 0, 1], ["y", 1, 0]]);
  });

  it("leaves skipped scenarios out of a category's pass rate, as for the run", () => {
    const [c] = categoryBreakdown([row(3, "a", "PASS"), row(3, "b", "SKIPPED_NO_MODEL")], [], []);
    expect(c).toMatchObject({ passRate: 1, scenarios: 1 });
  });
});

describe("filters", () => {
  it("classifies a tag dispatched by hand as a release candidate, not Manual", async () => {
    const { TAG_REF } = await import("../src/evals");
    expect(TAG_REF.test("v0.25.1-rc3")).toBe(true);
    expect(TAG_REF.test("v0.25.0")).toBe(true);
    expect(TAG_REF.test("main")).toBe(false);
    expect(TAG_REF.test("v0.25-fix")).toBe(false);
    expect(runKind({ event: "workflow_dispatch", is_tag: 1, ref: "v0.25.1-rc3", tier: null })).toBe("Release candidate");
  });

  it("filters runs by suite, trigger, tier and model, and a category narrows the counts", () => {
    const runs = [
      run(1, "2026-10-01T00:00:00Z", { event: "schedule" }),
      run(2, "2026-10-02T00:00:00Z", { event: "pull_request" }),
      run(3, "2026-10-03T00:00:00Z", { workflow: "SWE-bench (flagship)", event: "schedule" }),
    ];
    const rows = [row(1, "a", "PASS", { category: "x" }), row(1, "b", "FAIL", { category: "y" }), row(2, "a", "PASS"), row(3, "t", "FAIL", { kind: "task" })];
    const f = parseFilter(new URLSearchParams("trig=main"));
    expect(filterRuns(summarizeRuns(runs, rows), f).map((r) => r.run_id)).toEqual([1]);
    expect(filterRuns(summarizeRuns(runs, rows), parseFilter(new URLSearchParams("suite=swebench&trig=all"))).map((r) => r.run_id)).toEqual([3]);
    const [onlyX] = filterRuns(summarizeRuns(runs, rows, "x"), f);
    expect(onlyX).toMatchObject({ scenarios: 1, passRate: 1 });
  });
});

describe("collectEvals against real SQLite", () => {
  const evalEnv = (db: D1Database) => ({ DB: db, GITHUB_REPO: "amd/gaia", NPM_PACKAGE: "x", PYPI_PACKAGE: "y" });
  const ghRun = (id: number, attempt = 1, conclusion = "success") => ({
    id, event: "schedule", head_branch: "main", head_sha: "abc" + id, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    status: "completed", conclusion, html_url: "u", name: "Agent Eval — flagship", run_attempt: attempt,
  });
  async function github(runs: Record<string, ReturnType<typeof ghRun>[]>, zips: Record<number, Uint8Array | "gone">, calls: string[]) {
    return async (url: string) => {
      calls.push(url);
      const wf = Object.keys(runs).find((w) => url.includes(`/workflows/${w}/runs`));
      if (url.includes("/actions/workflows/")) return new Response(JSON.stringify({ workflow_runs: wf ? runs[wf] : [] }));
      const art = url.match(/runs\/(\d+)\/artifacts/);
      if (art) {
        const z = zips[Number(art[1])];
        return new Response(JSON.stringify({ artifacts: z === undefined ? [] : [{ id: Number(art[1]), name: "eval-lane", size_in_bytes: 100, expired: z === "gone" }] }));
      }
      const dl = url.match(/artifacts\/(\d+)\/zip/);
      if (dl) return new Response(zips[Number(dl[1])] as Uint8Array);
      return new Response("not found", { status: 404 });
    };
  }
  const zipWith = (status: string) =>
    makeZip({ "lane-status.json": JSON.stringify({ lane: "l1", tier: "smoke" }), "cat/scorecard.json": JSON.stringify({ scenarios: [scenario({ status })] }) });

  it("re-reads a run when GitHub reports a newer attempt, and skips it otherwise", async () => {
    const { memoryD1 } = await import("./d1");
    const { collectEvals } = await import("../src/evals");
    const db = memoryD1();
    const calls: string[] = [];
    await collectEvals(evalEnv(db), await github({ "eval_flagship.yml": [ghRun(1, 1)] }, { 1: await zipWith("FAIL") }, calls), 14, new Date());
    expect((await db.prepare("SELECT status FROM eval_scenarios").first())).toEqual({ status: "FAIL" });
    calls.length = 0;
    await collectEvals(evalEnv(db), await github({ "eval_flagship.yml": [ghRun(1, 1)] }, { 1: await zipWith("FAIL") }, calls), 14, new Date());
    expect(calls.some((c) => c.includes("/artifacts"))).toBe(false);
    await collectEvals(evalEnv(db), await github({ "eval_flagship.yml": [ghRun(1, 2)] }, { 1: await zipWith("PASS") }, calls), 14, new Date());
    expect((await db.prepare("SELECT e.status, r.run_attempt FROM eval_scenarios e JOIN eval_runs r USING(run_id)").first())).toEqual({ status: "PASS", run_attempt: 2 });
  });

  it("keeps going past an unreadable run, stores the readable ones, and reports the failure", async () => {
    const { memoryD1 } = await import("./d1");
    const { collectEvals } = await import("../src/evals");
    const db = memoryD1();
    const runs = { "eval_flagship.yml": [ghRun(1), ghRun(2)] };
    await expect(collectEvals(evalEnv(db), await github(runs, { 1: "gone", 2: await zipWith("PASS") }, []), 14, new Date())).rejects.toThrow(/1 run\(s\) could not be read/);
    expect((await db.prepare("SELECT run_id FROM eval_runs").all()).results).toEqual([{ run_id: 2 }]);
  });

  it("remembers a run with no scorecards so it is not fetched again", async () => {
    const { memoryD1 } = await import("./d1");
    const { collectEvals } = await import("../src/evals");
    const db = memoryD1();
    const calls: string[] = [];
    await collectEvals(evalEnv(db), await github({ "eval_flagship.yml": [ghRun(3)] }, {}, calls), 14, new Date());
    calls.length = 0;
    await collectEvals(evalEnv(db), await github({ "eval_flagship.yml": [ghRun(3)] }, {}, calls), 14, new Date());
    expect(calls.some((c) => c.includes("/runs/3/artifacts"))).toBe(false);
  });
});

describe("cancelled eval runs", () => {
  it("are left out of the run summaries, since they only measured the lanes that finished", () => {
    const out = summarizeRuns([run(1, "2026-10-01T00:00:00Z", { conclusion: "cancelled" }), run(2, "2026-10-02T00:00:00Z")], [row(1, "a", "PASS"), row(2, "a", "FAIL")]);
    expect(out.map((r) => r.run_id)).toEqual([2]);
  });

  it("cost per scenario counts only scenarios that report a cost", () => {
    const [s] = summarizeRuns([run(1, "2026-10-01T00:00:00Z")], [row(1, "a", "PASS", { cost_usd: 0.4 }), row(1, "b", "PASS", { cost_usd: null })]);
    expect(s!.costPerAttempted).toBeCloseTo(0.4);
  });
});
