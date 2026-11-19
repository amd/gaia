/**
 * Aggregations behind the Evals page. Pure functions over stored rows.
 *
 * A "run" on the page is one GitHub run's results for one suite, tier and
 * model: a workflow that runs the same suite against two models produces two
 * runs. Runs are compared only within a series: the same suite, tier and model.
 *
 * Pass rate is PASS out of PASS + FAIL + TIMEOUT. ERROR (the harness or
 * infrastructure broke) and SKIPPED are counted but left out of it.
 */

import { PROJECT, RC_TAG, RELEASE_TAG } from "./config";
import { median } from "./momentum";

export interface EvalRunRow {
  run_id: number;
  workflow: string;
  event: string;
  ref: string | null;
  sha: string;
  created_at: string;
  conclusion: string | null;
  html_url: string;
}
export interface EvalResultRow {
  run_id: number;
  suite: string;
  /** '' when the file gave none (as for model and category). */
  tier: string;
  model: string;
  category: string;
  result_id: string;
  status: string;
  score: number | null;
  elapsed_s: number | null;
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  note: string | null;
}

/** Statuses with a pass-or-fail outcome: the pass rate's denominator. */
export const DECIDED = new Set(["PASS", "FAIL", "TIMEOUT"]);
const failed = (s: string) => s === "FAIL" || s === "TIMEOUT";

export type RunKind = "Release" | "Release candidate" | "Scheduled" | "Push" | "Pull request" | "Manual" | "Other";

/** What triggered the run. A run on a release tag counts as a release whatever event started it. */
export function runKind(r: Pick<EvalRunRow, "event" | "ref">): RunKind {
  if (r.event === "pull_request" || r.event === "pull_request_target") return "Pull request";
  if (r.ref && RELEASE_TAG.test(r.ref)) return RC_TAG.test(r.ref) ? "Release candidate" : "Release";
  if (r.event === "schedule") return "Scheduled";
  if (r.event === "push") return "Push";
  if (r.event === "workflow_dispatch") return "Manual";
  return "Other";
}

export const TRIGGERS = ["main", "scheduled", "release", "rc", "pr", "manual", "all"] as const;
export type Trigger = (typeof TRIGGERS)[number];

/** `main` is what landed: scheduled and push runs on the default branch, and releases. */
export function matchesTrigger(r: Pick<EvalRunRow, "event" | "ref">, trig: Trigger): boolean {
  const k = runKind(r);
  switch (trig) {
    case "main":
      return k === "Release" || k === "Release candidate" || ((k === "Scheduled" || k === "Push") && r.ref === PROJECT.defaultBranch);
    case "scheduled":
      return k === "Scheduled";
    case "release":
      return k === "Release";
    case "rc":
      return k === "Release candidate";
    case "pr":
      return k === "Pull request";
    case "manual":
      return k === "Manual";
    case "all":
      return true;
  }
}

export interface EvalFilter {
  suite: string;
  trig: Trigger;
  tier: string;
  model: string;
  category: string;
}

/** Filter from the query string; with no suite asked for, the first of `suites` (the most-run one). */
export function parseFilter(q: URLSearchParams, suites: string[]): EvalFilter {
  const trig = q.get("trig") ?? "main";
  return {
    suite: q.get("suite") || suites[0] || "",
    trig: (TRIGGERS as readonly string[]).includes(trig) ? (trig as Trigger) : "main",
    tier: q.get("tier") ?? "",
    model: q.get("model") ?? "",
    category: q.get("cat") ?? "",
  };
}

export interface RunSummary extends EvalRunRow {
  /** "<run id>.<n>": n numbers the run's tier and model pairs of this suite, in sorted order. */
  key: string;
  kind: RunKind;
  suite: string;
  tier: string;
  model: string;
  results: number;
  passed: number;
  failed: number;
  timeout: number;
  errors: number;
  skipped: number;
  passRate: number | null;
  avgScore: number | null;
  costUsd: number | null;
  costPerResult: number | null;
  medianSeconds: number | null;
  totalSeconds: number | null;
  categories: number;
  /** Against the previous run of the same series; null when there is none. */
  regressed: number | null;
  fixed: number | null;
}

const avg = (xs: (number | null)[]): number | null => {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
const sum = (xs: (number | null)[]): number | null => {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) : null;
};
function groupBy<T>(xs: T[], key: (x: T) => string): Map<string, T[]> {
  const m = new Map<string, T[]>();
  for (const x of xs) {
    const k = key(x);
    const list = m.get(k);
    if (list) list.push(x);
    else m.set(k, [x]);
  }
  return m;
}

const SEP = "\u0000";
const groupOf = (r: { run_id: number; tier: string; model: string }) => `${r.run_id}${SEP}${r.tier}${SEP}${r.model}`;
const seriesOf = (r: { tier: string; model: string }) => `${r.tier}${SEP}${r.model}`;
const resultOf = (r: Pick<EvalResultRow, "category" | "result_id">) => `${r.category}${SEP}${r.result_id}`;

export function passRate(rows: Pick<EvalResultRow, "status">[]): number | null {
  const decided = rows.filter((x) => DECIDED.has(x.status));
  return decided.length ? decided.filter((x) => x.status === "PASS").length / decided.length : null;
}

/** Page key of every (run, tier, model) in `rows` (one suite). Built from unfiltered rows so keys survive a category filter. */
export function runKeys(rows: EvalResultRow[]): Map<string, string> {
  const byRun = new Map<number, Set<string>>();
  for (const r of rows) {
    const s = byRun.get(r.run_id) ?? new Set<string>();
    s.add(seriesOf(r));
    byRun.set(r.run_id, s);
  }
  const out = new Map<string, string>();
  for (const [run, set] of byRun) [...set].sort().forEach((s, i) => out.set(`${run}${SEP}${s}`, `${run}.${i}`));
  return out;
}

/**
 * One summary per run, tier and model in `rows` (one suite), oldest first.
 * Cancelled runs are left out: they measured only what finished before they
 * stopped. With `category`, every number covers that category only.
 */
export function summarizeRuns(runs: EvalRunRow[], rows: EvalResultRow[], category = ""): RunSummary[] {
  const keys = runKeys(rows);
  const byId = new Map(runs.map((r) => [r.run_id, r]));
  const out: RunSummary[] = [];
  for (const [g, list] of groupBy(category ? rows.filter((r) => r.category === category) : rows, groupOf)) {
    const run = byId.get(list[0]!.run_id);
    if (!run || run.conclusion === "cancelled") continue;
    const count = (s: string) => list.filter((x) => x.status === s).length;
    const cost = sum(list.map((x) => x.cost_usd));
    const secs = list.map((x) => x.elapsed_s).filter((x): x is number => x != null);
    out.push({
      ...run,
      key: keys.get(g)!,
      kind: runKind(run),
      suite: list[0]!.suite,
      tier: list[0]!.tier,
      model: list[0]!.model,
      results: list.length,
      passed: count("PASS"),
      failed: count("FAIL"),
      timeout: count("TIMEOUT"),
      errors: count("ERROR"),
      skipped: count("SKIPPED"),
      passRate: passRate(list),
      avgScore: avg(list.map((x) => x.score)),
      costUsd: cost,
      // Over the results that report a cost: dividing by every result would understate it.
      costPerResult: cost != null ? cost / list.filter((x) => x.cost_usd != null).length : null,
      medianSeconds: secs.length ? median(secs) : null,
      totalSeconds: secs.length ? secs.reduce((a, b) => a + b, 0) : null,
      categories: new Set(list.map((x) => x.category)).size,
      regressed: null,
      fixed: null,
    });
  }
  return out.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.key.localeCompare(b.key));
}

export interface Previous {
  status: string;
  key: string;
  run_id: number;
}

/**
 * Compare every run with the earlier runs of its series. `runs` must already
 * be the comparable ones (trigger filter applied), oldest first. A result's
 * previous status is its last pass-or-fail outcome in an earlier run of the
 * series, so an ERROR in between neither hides nor fakes a regression.
 * Regressed: PASS before, FAIL or TIMEOUT now. Fixed: the reverse.
 */
export function compareRuns(
  runs: RunSummary[],
  rows: EvalResultRow[],
  keys = runKeys(rows),
): { runs: RunSummary[]; previous: Map<string, Map<string, Previous>>; earlier: Map<string, RunSummary[]> } {
  const rowsByKey = groupBy(rows, (r) => keys.get(groupOf(r)) ?? "");
  const last = new Map<string, Map<string, Previous>>();
  const seen = new Map<string, RunSummary[]>();
  const previous = new Map<string, Map<string, Previous>>();
  const earlier = new Map<string, RunSummary[]>();
  const out = runs.map((m) => {
    const s = seriesOf(m);
    const before = seen.get(s) ?? [];
    const lastOf = last.get(s) ?? new Map<string, Previous>();
    const mine = rowsByKey.get(m.key) ?? [];
    const prev = new Map<string, Previous>();
    let regressed = 0;
    let fixed = 0;
    for (const r of mine) {
      const p = lastOf.get(resultOf(r));
      if (!p) continue;
      prev.set(resultOf(r), p);
      if (p.status === "PASS" && failed(r.status)) regressed++;
      if (failed(p.status) && r.status === "PASS") fixed++;
    }
    for (const r of mine) if (DECIDED.has(r.status)) lastOf.set(resultOf(r), { status: r.status, key: m.key, run_id: m.run_id });
    previous.set(m.key, prev);
    earlier.set(m.key, [...before]);
    seen.set(s, [...before, m]);
    last.set(s, lastOf);
    return before.length ? { ...m, regressed, fixed } : m;
  });
  return { runs: out, previous, earlier };
}

export interface ResultDetail extends EvalResultRow {
  previous: string | null;
  previousRun: number | null;
}

export function resultDetails(selected: EvalResultRow[], previous: Map<string, Previous> | undefined): ResultDetail[] {
  return selected
    .map((r) => {
      const p = previous?.get(resultOf(r));
      return { ...r, previous: p?.status ?? null, previousRun: p?.run_id ?? null };
    })
    .sort((a, b) => a.category.localeCompare(b.category) || a.result_id.localeCompare(b.result_id));
}

export interface CategoryRow {
  category: string;
  decided: number;
  passed: number;
  passRate: number | null;
  prevPassRate: number | null;
}

/** Pass rate per category of one run, against the newest earlier run of its series that measured the category. */
export function categoryBreakdown(selected: EvalResultRow[], earlierRowsNewestFirst: EvalResultRow[][]): CategoryRow[] {
  return [...groupBy(selected, (r) => r.category).entries()]
    .map(([category, list]) => {
      const prev = earlierRowsNewestFirst.map((rs) => rs.filter((r) => r.category === category)).find((rs) => rs.some((r) => DECIDED.has(r.status)));
      return {
        category,
        decided: list.filter((x) => DECIDED.has(x.status)).length,
        passed: list.filter((x) => x.status === "PASS").length,
        passRate: passRate(list),
        prevPassRate: prev ? passRate(prev) : null,
      };
    })
    .sort((a, b) => (a.passRate ?? 2) - (b.passRate ?? 2) || a.category.localeCompare(b.category));
}

export interface Reliability {
  result_id: string;
  category: string;
  runs: number;
  passed: number;
  failed: number;
  /** ERROR and SKIPPED: no pass-or-fail outcome. */
  undecided: number;
  passRate: number | null;
  flaky: boolean;
  lastStatus: string;
  avgSeconds: number | null;
}

/** Per-result record across the given runs (oldest first), least reliable first. */
export function reliability(runs: RunSummary[], rows: EvalResultRow[], keys = runKeys(rows)): Reliability[] {
  const order = new Map(runs.map((m, i) => [m.key, i]));
  const mine = rows.filter((r) => order.has(keys.get(groupOf(r)) ?? ""));
  const at = (r: EvalResultRow) => order.get(keys.get(groupOf(r))!)!;
  const out: Reliability[] = [];
  for (const list of groupBy(mine, resultOf).values()) {
    list.sort((a, b) => at(a) - at(b));
    const passed = list.filter((x) => x.status === "PASS").length;
    const fails = list.filter((x) => failed(x.status)).length;
    const last = list[list.length - 1]!;
    out.push({
      result_id: last.result_id,
      category: last.category,
      runs: list.length,
      passed,
      failed: fails,
      undecided: list.length - passed - fails,
      passRate: passed + fails ? passed / (passed + fails) : null,
      flaky: passed > 0 && fails > 0,
      lastStatus: last.status,
      avgSeconds: avg(list.map((x) => x.elapsed_s)),
    });
  }
  return out.sort((a, b) => (a.passRate ?? 2) - (b.passRate ?? 2) || b.failed - a.failed || a.result_id.localeCompare(b.result_id));
}

export interface EvalView {
  suite: string;
  filter: EvalFilter;
  options: { tiers: string[]; models: string[]; categories: string[] };
  runs: RunSummary[];
  selected: string | null;
  results: ResultDetail[];
  byCategory: CategoryRow[];
  reliability: Reliability[];
  medianSeconds: number | null;
}

/**
 * Everything the page shows for one suite. `runs` and `rows` cover the range
 * plus earlier runs of the suite (so the first runs in range have something to
 * compare with); only runs created at or after `since` are shown.
 */
export function evalView(runs: EvalRunRow[], rows: EvalResultRow[], f: EvalFilter, since: string, requested: string | null): EvalView {
  const suiteRows = rows.filter((r) => r.suite === f.suite);
  const byId = new Map(runs.map((r) => [r.run_id, r]));
  const rangeRows = suiteRows.filter((r) => {
    const run = byId.get(r.run_id);
    return !!run && run.created_at >= since && run.conclusion !== "cancelled" && matchesTrigger(run, f.trig);
  });
  const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort();
  const options = {
    tiers: uniq(rangeRows.map((r) => r.tier)),
    models: uniq(rangeRows.map((r) => r.model)),
    categories: uniq(rangeRows.filter((r) => (!f.tier || r.tier === f.tier) && (!f.model || r.model === f.model)).map((r) => r.category)),
  };
  // Keys come from every row of the suite, so a category filter cannot renumber a run.
  const keys = runKeys(suiteRows);
  const catRows = f.category ? suiteRows.filter((r) => r.category === f.category) : suiteRows;
  const comparable = summarizeRuns(runs, suiteRows, f.category).filter((m) => matchesTrigger(m, f.trig));
  const { runs: compared, previous, earlier } = compareRuns(comparable, catRows, keys);
  const shown = compared.filter((m) => m.created_at >= since && (!f.tier || m.tier === f.tier) && (!f.model || m.model === f.model));
  const selected = shown.find((m) => m.key === requested)?.key ?? shown.at(-1)?.key ?? null;
  const rowsOf = (key: string) => catRows.filter((r) => keys.get(groupOf(r)) === key);
  const selRows = selected ? rowsOf(selected) : [];
  const shownKeys = new Set(shown.map((m) => m.key));
  const shownRows = catRows.filter((r) => shownKeys.has(keys.get(groupOf(r)) ?? ""));
  const secs = shownRows.map((r) => r.elapsed_s).filter((x): x is number => x != null);
  return {
    suite: f.suite,
    filter: f,
    options,
    runs: shown,
    selected,
    results: selected ? resultDetails(selRows, previous.get(selected)) : [],
    byCategory: selected ? categoryBreakdown(selRows, [...(earlier.get(selected) ?? [])].reverse().map((m) => rowsOf(m.key))) : [],
    reliability: reliability(shown, shownRows, keys).slice(0, 300),
    medianSeconds: secs.length ? median(secs) : null,
  };
}
