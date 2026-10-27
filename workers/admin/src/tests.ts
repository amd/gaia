// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Test counts over time. The unit-test workflow does not upload a JUnit file,
 * so the count is read from pytest's own final summary line in the job log
 * ("=== 1234 passed, 5 skipped in 312.4s ==="). When a run has several jobs
 * (a matrix) the job with the most tests is used, so the same suite is not
 * counted once per Python version.
 */

import { batchRun, ghHeaders, githubDownload, type CollectEnv, type Fetcher, utcDay } from "./collector";

export interface TestCounts {
  passed: number;
  failed: number;
  skipped: number;
  errors: number;
  total: number;
}

const ANSI = /\u001b\[[0-9;]*m/g;
// GitHub prefixes each log line with an ISO timestamp.
const TIMESTAMP = /^\d{4}-\d\d-\d\dT[\d:.]+Z\s/;
// pytest's final summary: framed by "=" on both sides, e.g. "==== 5 passed, 1 skipped in 2.04s ====".
const FRAMED = /^=+ (.*\bin \d+(?:\.\d+)?s\b.*?) =+\s*$/;
const COUNT = (word: string): RegExp => new RegExp(`(\\d+) ${word}\\b`);

/**
 * The counts from the largest pytest summary in a log, or null when there is
 * none. A job may run pytest several times (the main suite and smaller
 * follow-ups), so the largest is the suite; requiring the "=" frame at the
 * start of the line keeps test names that quote summaries from matching.
 */
export function parsePytestSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const m = FRAMED.exec(raw.replace(ANSI, "").replace(TIMESTAMP, "").trimEnd());
    if (!m) continue;
    const body = m[1]!;
    const n = (word: string): number => Number(COUNT(word).exec(body)?.[1] ?? 0);
    const passed = n("passed");
    const failed = n("failed");
    const skipped = n("skipped");
    const errors = n("errors?");
    const total = passed + failed + skipped + errors + n("xfailed") + n("xpassed");
    if (total > 0 && (!best || total > best.total)) best = { passed, failed, skipped, errors, total };
  }
  return best;
}

/** The latest run per UTC day, from runs sorted in any order. */
export function latestRunPerDay(runs: { id: number; created_at: string }[]): { id: number; day: string }[] {
  const best = new Map<string, { id: number; created_at: string }>();
  for (const r of runs) {
    const day = r.created_at.slice(0, 10);
    const cur = best.get(day);
    if (!cur || r.created_at > cur.created_at) best.set(day, r);
  }
  return [...best.entries()].map(([day, r]) => ({ id: r.id, day })).sort((a, b) => a.day.localeCompare(b.day));
}

export async function jobLog(env: CollectEnv, f: Fetcher, jobId: number): Promise<string> {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/jobs/${jobId}/logs`;
  const res = await githubDownload(env, f, url);
  if (!res.ok) {
    throw new Error(
      `GET ${url} returned HTTP ${res.status}. Job logs need a token with Actions read access and expire after about 90 days.`,
    );
  }
  return res.text();
}

export async function collectTests(
  env: CollectEnv & { TEST_WORKFLOW?: string },
  f: Fetcher,
  days: number,
  today: Date,
  skipDays: number,
  refresh = false,
): Promise<string> {
  const workflow = env.TEST_WORKFLOW ?? "Unit Tests";
  const end = utcDay(new Date(today.getTime() - skipDays * 86_400_000));
  const start = utcDay(new Date(today.getTime() - (skipDays + days - 1) * 86_400_000));
  const rows = (
    await env.DB.prepare(
      "SELECT id, created_at FROM workflow_runs WHERE workflow = ? AND branch = 'main' AND event IN ('push','schedule') AND status = 'completed' AND conclusion IN ('success','failure') AND substr(created_at,1,10) BETWEEN ? AND ?",
    )
      .bind(workflow, start, end)
      .all<{ id: number; created_at: string }>()
  ).results;
  const have = new Set(
    (await env.DB.prepare("SELECT day FROM test_results WHERE day BETWEEN ? AND ?").bind(start, end).all<{ day: string }>()).results.map(
      (r) => r.day,
    ),
  );
  const stmts: D1PreparedStatement[] = [];
  let read = 0;
  let missing = 0;
  for (const { id, day } of latestRunPerDay(rows)) {
    if (have.has(day) && day !== end && !refresh) continue;
    const jobs = await (async () => {
      const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/runs/${id}/jobs?per_page=100`;
      const res = await f(url, { headers: ghHeaders(env) });
      if (!res.ok) throw new Error(`GET ${url} returned HTTP ${res.status}.`);
      return ((await res.json()) as { jobs: { id: number; conclusion: string | null }[] }).jobs;
    })();
    let best: TestCounts | null = null;
    for (const j of jobs) {
      if (j.conclusion !== "success" && j.conclusion !== "failure") continue;
      const counts = parsePytestSummary(await jobLog(env, f, j.id));
      if (counts && (!best || counts.total > best.total)) best = counts;
    }
    if (!best) {
      missing++;
      continue;
    }
    read++;
    stmts.push(
      env.DB.prepare(
        "INSERT OR REPLACE INTO test_results (day, run_id, workflow, passed, failed, skipped, errors, total, jobs, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(day, id, workflow, best.passed, best.failed, best.skipped, best.errors, best.total, jobs.length, new Date().toISOString()),
    );
  }
  await batchRun(env.DB, stmts);
  return `${read} days of test counts from "${workflow}"${missing ? `, ${missing} run(s) had no pytest summary line` : ""}`;
}
