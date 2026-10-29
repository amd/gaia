// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Aggregations behind the Evals page. Pure functions over stored rows. */

import { median } from "./momentum";

export interface EvalRunRow {
  run_id: number;
  workflow: string;
  event: string;
  ref: string | null;
  is_tag: number;
  sha: string;
  created_at: string;
  conclusion: string | null;
  tier: string | null;
  agent_model: string | null;
  judge_model: string | null;
  minutes: number | null;
  html_url: string;
}
export interface EvalScenarioRow {
  run_id: number;
  category: string;
  scenario_id: string;
  status: string;
  score: number | null;
  elapsed_s: number | null;
  cost_usd: number | null;
  tps: number | null;
  ttft_s: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tool_calls: number | null;
  turns: number | null;
  root_cause: string | null;
  /** "task" rows come from task suites (SWE-bench, outcomes) and are scored separately. */
  kind: string;
}

/** Statuses that say something went wrong around the agent rather than in it. */
export const INFRA = new Set(["INFRA_ERROR", "SETUP_ERROR", "ERRORED", "BUDGET_EXCEEDED", "BLOCKED_BY_ARCHITECTURE"]);
const skipped = (s: string) => s.startsWith("SKIPPED");

/** What triggered the run, in the words the page uses. */
export function runKind(r: Pick<EvalRunRow, "event" | "is_tag" | "ref" | "tier">): string {
  if (r.is_tag) return /-rc\d+$/.test(r.ref ?? "") ? "Release candidate" : "Release";
  if (r.event === "schedule") return "Nightly";
  if (r.event === "pull_request") return "Pull request";
  if (r.event === "workflow_dispatch") return "Manual";
  return "Other";
}

export function isSweBench(r: Pick<EvalRunRow, "workflow">): boolean {
  return /SWE-bench/i.test(r.workflow);
}

/** tier.json's tier, else inferred: older runs predate tier.json. */
export function tierLabel(r: Pick<EvalRunRow, "tier" | "event">, attempted: number): string {
  return r.tier ?? (r.event === "pull_request" ? "pr" : attempted >= 150 ? "full" : "partial");
}

export interface EvalFilter {
  suite: "agent" | "swebench";
  trig: string;
  tier: string;
  model: string;
  category: string;
}

export function parseFilter(q: URLSearchParams): EvalFilter {
  return {
    suite: q.get("suite") === "swebench" ? "swebench" : "agent",
    trig: q.get("trig") || "main",
    tier: q.get("tier") || "",
    model: q.get("model") || "",
    category: q.get("cat") || "",
  };
}

export function matchesTrigger(kind: string, trig: string): boolean {
  switch (trig) {
    case "main":
      return kind === "Nightly" || kind === "Release" || kind === "Release candidate";
    case "nightly":
      return kind === "Nightly";
    case "release":
      return kind === "Release" || kind === "Release candidate";
    case "pr":
      return kind === "Pull request";
    case "manual":
      return kind === "Manual";
    default:
      return true;
  }
}

export interface RunSummary extends EvalRunRow {
  kind: string;
  tierLabel: string;
  scenarios: number;
  passed: number;
  failed: number;
  timeout: number;
  infra: number;
  skipped: number;
  other: number;
  passRate: number | null;
  judgedPassRate: number | null;
  avgScore: number | null;
  costUsd: number | null;
  costPerAttempted: number | null;
  avgTps: number | null;
  avgTtft: number | null;
  medianSeconds: number | null;
  categories: number;
  /** What the pass rate measures: judged scenarios, or task-suite tasks when the run has no scenarios. */
  measures: "scenarios" | "tasks";
  /** Task-suite results that ran alongside the scenarios (shown separately). */
  tasks: number;
  tasksPassed: number;
}

const avg = (xs: (number | null)[]): number | null => {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
const sum = (xs: (number | null)[]): number | null => {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) : null;
};

function groupBy<T>(xs: T[], key: (x: T) => string | number): Map<string | number, T[]> {
  const m = new Map<string | number, T[]>();
  for (const x of xs) {
    const k = key(x);
    const list = m.get(k);
    if (list) list.push(x);
    else m.set(k, [x]);
  }
  return m;
}

/** The rows a run is measured by: its scenarios, or its tasks when it has no scenarios. */
function measured(all: EvalScenarioRow[]): { rows: EvalScenarioRow[]; tasks: EvalScenarioRow[]; measures: "scenarios" | "tasks" } {
  const scen = all.filter((x) => x.kind !== "task");
  const tasks = all.filter((x) => x.kind === "task");
  return scen.length ? { rows: scen, tasks, measures: "scenarios" } : { rows: tasks, tasks: [], measures: "tasks" };
}

/**
 * Pass rate is PASS over every scenario attempted (the definition behind the
 * committed baseline and the 80% target); skipped scenarios were not attempted.
 * Judged pass rate also leaves out timeouts and infrastructure errors, so it
 * isolates the agent's answers. With `category` set, every number covers that
 * category only.
 */
export function summarizeRuns(runs: EvalRunRow[], rows: EvalScenarioRow[], category = ""): RunSummary[] {
  const by = groupBy(category ? rows.filter((r) => r.category === category) : rows, (r) => r.run_id);
  return runs
    .map((run) => {
      const { rows: s, tasks, measures } = measured(by.get(run.run_id) ?? []);
      const passed = s.filter((x) => x.status === "PASS").length;
      const failed = s.filter((x) => x.status === "FAIL").length;
      const timeout = s.filter((x) => x.status === "TIMEOUT").length;
      const infra = s.filter((x) => INFRA.has(x.status)).length;
      const skip = s.filter((x) => skipped(x.status)).length;
      const attempted = s.length - skip;
      const cost = sum(s.map((x) => x.cost_usd));
      return {
        ...run,
        kind: runKind(run),
        tierLabel: tierLabel(run, attempted),
        scenarios: s.length,
        passed,
        failed,
        timeout,
        infra,
        skipped: skip,
        other: s.length - passed - failed - timeout - infra - skip,
        passRate: attempted ? passed / attempted : null,
        judgedPassRate: passed + failed ? passed / (passed + failed) : null,
        avgScore: avg(s.map((x) => x.score)),
        costUsd: cost,
        // Over the scenarios that report a cost: dividing by every attempted one would understate it.
        costPerAttempted: cost != null ? cost / s.filter((x) => x.cost_usd != null).length : null,
        avgTps: avg(s.map((x) => x.tps)),
        avgTtft: avg(s.map((x) => x.ttft_s)),
        medianSeconds: median(s.map((x) => x.elapsed_s).filter((x): x is number => x != null)),
        categories: new Set(s.map((x) => x.category)).size,
        measures,
        tasks: tasks.length,
        tasksPassed: tasks.filter((x) => x.status === "PASS").length,
      };
    })
    // A cancelled run measured only the lanes that finished before it stopped, so its pass rate is not comparable.
    .filter((r) => r.scenarios > 0 && r.conclusion !== "cancelled")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** Runs that pass the page's filters (the category filter applies to rows, in summarizeRuns). */
export function filterRuns(summaries: RunSummary[], f: EvalFilter): RunSummary[] {
  return summaries.filter(
    (r) =>
      (f.suite === "swebench") === isSweBench(r) &&
      matchesTrigger(r.kind, f.trig) &&
      (!f.tier || r.tierLabel === f.tier) &&
      (!f.model || r.agent_model === f.model),
  );
}

export interface ScenarioDetail extends EvalScenarioRow {
  previous: string | null;
  previousRun: number | null;
}

/**
 * The selected run's rows, each with its status in the most recent earlier run
 * that included it. The caller passes only comparable earlier runs.
 */
export function scenarioDetails(selected: EvalScenarioRow[], earlierRuns: EvalRunRow[], earlierRows: EvalScenarioRow[]): ScenarioDetail[] {
  const when = new Map(earlierRuns.map((r) => [r.run_id, r.created_at]));
  const prev = new Map<string, { status: string; run: number; at: string }>();
  for (const r of earlierRows) {
    const t = when.get(r.run_id);
    if (!t) continue;
    const key = `${r.category}/${r.scenario_id}`;
    const cur = prev.get(key);
    if (!cur || t > cur.at) prev.set(key, { status: r.status, run: r.run_id, at: t });
  }
  return selected
    .map((r) => {
      const p = prev.get(`${r.category}/${r.scenario_id}`);
      return { ...r, previous: p?.status ?? null, previousRun: p?.run ?? null };
    })
    .sort((a, b) => a.category.localeCompare(b.category) || a.scenario_id.localeCompare(b.scenario_id));
}

export interface ScenarioReliability {
  scenario_id: string;
  category: string;
  runs: number;
  passed: number;
  failed: number;
  notJudged: number;
  passRate: number | null;
  flaky: boolean;
  lastStatus: string;
  lastRun: number;
  avgScore: number | null;
  avgSeconds: number | null;
}

/**
 * Per-scenario record across the given runs, worst first. Only PASS and FAIL
 * count toward the pass rate and flakiness; infrastructure errors and skips are
 * counted separately, since they say nothing about the agent.
 */
export function scenarioReliability(runs: EvalRunRow[], rows: EvalScenarioRow[]): ScenarioReliability[] {
  const order = new Map(runs.map((r) => [r.run_id, r.created_at]));
  const by = groupBy(rows.filter((r) => order.has(r.run_id)), (r) => `${r.category}/${r.scenario_id}`);
  const out: ScenarioReliability[] = [];
  for (const list of by.values()) {
    list.sort((a, b) => order.get(a.run_id)!.localeCompare(order.get(b.run_id)!));
    const passed = list.filter((x) => x.status === "PASS").length;
    const failed = list.filter((x) => x.status === "FAIL").length;
    const last = list[list.length - 1]!;
    out.push({
      scenario_id: last.scenario_id,
      category: last.category,
      runs: list.length,
      passed,
      failed,
      notJudged: list.length - passed - failed,
      passRate: passed + failed ? passed / (passed + failed) : null,
      flaky: passed > 0 && failed > 0,
      lastStatus: last.status,
      lastRun: last.run_id,
      avgScore: avg(list.map((x) => x.score)),
      avgSeconds: avg(list.map((x) => x.elapsed_s)),
    });
  }
  return out.sort(
    (a, b) => (a.passRate ?? 2) - (b.passRate ?? 2) || b.failed - a.failed || a.scenario_id.localeCompare(b.scenario_id),
  );
}

export interface CategoryRow {
  category: string;
  scenarios: number;
  passed: number;
  passRate: number | null;
  avgScore: number | null;
  prevPassRate: number | null;
}

/**
 * Pass rate per category for one run (skipped scenarios left out, as for the
 * run), against the most recent earlier comparable run that measured it.
 */
export function categoryBreakdown(selected: EvalScenarioRow[], earlierRuns: EvalRunRow[], earlierRows: EvalScenarioRow[]): CategoryRow[] {
  const rate = (list: EvalScenarioRow[]) => {
    const att = list.filter((x) => !skipped(x.status));
    return att.length ? att.filter((x) => x.status === "PASS").length / att.length : null;
  };
  const newestFirst = [...earlierRuns].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const byRunCat = groupBy(earlierRows, (r) => `${r.run_id}/${r.category}`);
  return [...groupBy(selected, (r) => r.category).entries()]
    .map(([category, list]) => {
      let prevPassRate: number | null = null;
      for (const r of newestFirst) {
        const p = byRunCat.get(`${r.run_id}/${category}`);
        if (p) {
          prevPassRate = rate(p);
          break;
        }
      }
      return {
        category: String(category),
        scenarios: list.filter((x) => !skipped(x.status)).length,
        passed: list.filter((x) => x.status === "PASS").length,
        passRate: rate(list),
        avgScore: avg(list.map((x) => x.score)),
        prevPassRate,
      };
    })
    .sort((a, b) => (a.passRate ?? 2) - (b.passRate ?? 2) || a.category.localeCompare(b.category));
}
