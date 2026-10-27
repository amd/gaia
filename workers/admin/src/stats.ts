// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { classifyAsset, isMainOrRcTag, isRcTag } from "./classify";

export interface ReleaseRow {
  tag: string;
  published_at: string;
  prerelease: number;
  html_url: string;
}
export interface AssetRow {
  tag: string;
  asset: string;
  downloads: number;
}

export interface ReleaseDownloads {
  tag: string;
  publishedAt: string;
  rc: boolean;
  // Main product line (vX.Y.Z or vX.Y.Z-rcN) vs. sub-package tags.
  main: boolean;
  install: number;
  meta: number;
  byOs: Record<string, number>;
  byProduct: Record<string, number>;
}

/** Per-release download totals, split install files vs. updater/metadata files. */
export function releaseDownloads(releases: ReleaseRow[], assets: AssetRow[]): ReleaseDownloads[] {
  const out = new Map<string, ReleaseDownloads>();
  for (const r of releases) {
    out.set(r.tag, {
      tag: r.tag,
      publishedAt: r.published_at,
      rc: isRcTag(r.tag),
      main: isMainOrRcTag(r.tag),
      install: 0,
      meta: 0,
      byOs: {},
      byProduct: {},
    });
  }
  for (const a of assets) {
    const rel = out.get(a.tag);
    if (!rel) continue;
    const c = classifyAsset(a.asset);
    if (c.kind === "meta") {
      rel.meta += a.downloads;
      continue;
    }
    rel.install += a.downloads;
    rel.byOs[c.os] = (rel.byOs[c.os] ?? 0) + a.downloads;
    rel.byProduct[c.product] = (rel.byProduct[c.product] ?? 0) + a.downloads;
  }
  return [...out.values()].sort((a, b) => a.publishedAt.localeCompare(b.publishedAt));
}

export interface DailyPoint {
  day: string;
  install: number;
  meta: number;
}

/**
 * Daily download deltas from consecutive snapshot days. Days without a
 * snapshot are skipped (a gap, not a zero), and a delta spanning a gap is
 * dropped rather than spread. A negative delta (an asset or release was
 * deleted) is reported separately so it is visible instead of clipped.
 */
export function dailyDeltas(
  rows: { day: string; tag: string; asset: string; downloads: number }[],
): { points: DailyPoint[]; negativeDays: string[] } {
  const byDay = new Map<string, Map<string, number>>();
  for (const r of rows) {
    const m = byDay.get(r.day) ?? new Map<string, number>();
    m.set(`${r.tag}\u0000${r.asset}`, r.downloads);
    byDay.set(r.day, m);
  }
  const days = [...byDay.keys()].sort();
  const points: DailyPoint[] = [];
  const negativeDays: string[] = [];
  for (let i = 1; i < days.length; i++) {
    const prevDay = days[i - 1]!;
    const day = days[i]!;
    if (Date.parse(day) - Date.parse(prevDay) !== 86_400_000) continue;
    const prev = byDay.get(prevDay)!;
    const cur = byDay.get(day)!;
    let install = 0;
    let meta = 0;
    let negative = false;
    for (const [key, n] of cur) {
      const delta = n - (prev.get(key) ?? 0);
      if (delta < 0) {
        negative = true;
        continue;
      }
      const asset = key.split("\u0000")[1]!;
      if (classifyAsset(asset).kind === "meta") meta += delta;
      else install += delta;
    }
    for (const key of prev.keys()) if (!cur.has(key)) negative = true;
    if (negative) negativeDays.push(day);
    points.push({ day, install, meta });
  }
  return { points, negativeDays };
}

export interface RunRow {
  id: number;
  workflow: string;
  branch: string | null;
  event: string;
  status: string;
  conclusion: string | null;
  head_sha: string;
  run_attempt: number;
  created_at: string;
  duration_s: number | null;
}

export interface WorkflowStats {
  workflow: string;
  runs: number;
  passed: number;
  failed: number;
  passRate: number | null;
  p50: number | null;
  p90: number | null;
  totalSeconds: number;
  /** Runs that took longer than the long-run limit (waiting for approval or a runner); left out of the times. */
  longRuns: number;
  flaky: number;
}

export function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)]!;
}

/**
 * Pass rate counts only decided runs (success or failure): cancelled and
 * skipped runs say nothing about health. A commit is flaky for a workflow when
 * that workflow failed on it and a later run (or re-run attempt) passed.
 */
export function ciStats(runs: RunRow[], longRunS = 6 * 3600): {
  workflows: WorkflowStats[];
  /** Distinct commits with a decided run, the denominator for the flaky rate. */
  commits: number;
  /** Distinct commits that were flaky in at least one workflow. */
  flakyCommits: number;
} {
  const byWf = new Map<string, RunRow[]>();
  for (const r of runs) {
    const list = byWf.get(r.workflow) ?? [];
    list.push(r);
    byWf.set(r.workflow, list);
  }
  const workflows: WorkflowStats[] = [];
  const flakyShas = new Set<string>();
  for (const [workflow, list] of byWf) {
    const decided = list.filter((r) => r.conclusion === "success" || r.conclusion === "failure");
    const passed = decided.filter((r) => r.conclusion === "success").length;
    const durations = list
      .filter((r) => r.conclusion === "success" && r.duration_s !== null && r.duration_s <= longRunS)
      .map((r) => r.duration_s as number)
      .sort((a, b) => a - b);
    const bySha = new Map<string, RunRow[]>();
    for (const r of decided) bySha.set(r.head_sha, [...(bySha.get(r.head_sha) ?? []), r]);
    let flaky = 0;
    for (const [sha, rs] of bySha) {
      rs.sort((x, y) => x.created_at.localeCompare(y.created_at) || x.run_attempt - y.run_attempt);
      const firstFail = rs.findIndex((r) => r.conclusion === "failure");
      // Only a failure followed by a pass on a later run counts. A re-run keeps its run id and GitHub
      // replaces the earlier attempt, so a failure fixed by re-running the same run is not visible here.
      const isFlaky = firstFail >= 0 && rs.slice(firstFail + 1).some((r) => r.conclusion === "success");
      if (isFlaky) {
        flaky++;
        flakyShas.add(sha);
      }
    }
    workflows.push({
      workflow,
      runs: list.length,
      passed,
      failed: decided.length - passed,
      passRate: decided.length ? passed / decided.length : null,
      p50: percentile(durations, 50),
      p90: percentile(durations, 90),
      totalSeconds: list.reduce((a, r) => a + (r.duration_s != null && r.duration_s <= longRunS ? r.duration_s : 0), 0),
      longRuns: list.filter((r) => r.duration_s != null && r.duration_s > longRunS).length,
      flaky,
    });
  }
  workflows.sort((a, b) => b.failed - a.failed || b.runs - a.runs);
  const commits = new Set(runs.filter((r) => r.conclusion === "success" || r.conclusion === "failure").map((r) => r.head_sha)).size;
  return { workflows, commits, flakyCommits: flakyShas.size };
}
