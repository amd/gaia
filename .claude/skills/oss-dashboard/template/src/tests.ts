/**
 * Test counts over time, read from the test runner's own summary in the job
 * log of TESTS.workflow (config.ts), so no workflow change or uploaded report
 * is needed. When a run has several jobs (a matrix) the job with the most
 * tests is used, so the same suite is not counted once per OS or version.
 */

import { DEFAULT_BRANCH_BUILD_SQL } from "./build";
import { PROJECT, TESTS } from "./config";
import { ghHeaders, githubDownload, insertMany, writeAtomic, type CollectEnv, type Fetcher, utcDay } from "./collector";

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

const clean = (raw: string): string => raw.replace(ANSI, "").replace(TIMESTAMP, "").trimEnd();
const larger = (a: TestCounts | null, b: TestCounts | null): TestCounts | null => (!a || (b && b.total > a.total) ? b : a);
const addCounts = (a: TestCounts | null, b: TestCounts | null): TestCounts | null =>
  !a ? b : !b ? a : { passed: a.passed + b.passed, failed: a.failed + b.failed, skipped: a.skipped + b.skipped, errors: a.errors + b.errors, total: a.total + b.total };
const counts = (passed: number, failed: number, skipped: number, errors = 0, extra = 0): TestCounts | null => {
  const total = passed + failed + skipped + errors + extra;
  return total > 0 ? { passed, failed, skipped, errors, total } : null;
};

/**
 * The counts from the largest pytest summary in a log, or null when there is
 * none. A job may run pytest several times (the main suite and smaller
 * follow-ups), so the largest is the suite; requiring the "=" frame at the
 * start of the line keeps test names that quote summaries from matching.
 */
export function parsePytestSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const m = FRAMED.exec(clean(raw));
    if (!m) continue;
    const body = m[1]!;
    const n = (word: string): number => Number(COUNT(word).exec(body)?.[1] ?? 0);
    best = larger(best, counts(n("passed"), n("failed"), n("skipped"), n("errors?"), n("xfailed") + n("xpassed")));
  }
  return best;
}

/** Jest: "Tests:       1 failed, 2 skipped, 40 passed, 43 total". The largest run in the log is the suite. */
export function parseJestSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const line = clean(raw).trim();
    if (!/^Tests:\s/.test(line) || !/\d+ total/.test(line)) continue;
    const n = (word: string): number => Number(COUNT(word).exec(line)?.[1] ?? 0);
    best = larger(best, counts(n("passed"), n("failed"), n("skipped") + n("todo"), 0));
  }
  return best;
}

/** Vitest: "Tests  2 failed | 235 passed | 3 skipped (240)". The largest run in the log is the suite. */
export function parseVitestSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const line = clean(raw).trim();
    const m = /^Tests\s+(.*)\((\d+)\)\s*$/.exec(line);
    if (!m) continue;
    const n = (word: string): number => Number(COUNT(word).exec(m[1]!)?.[1] ?? 0);
    best = larger(best, counts(n("passed"), n("failed"), n("skipped") + n("todo"), 0));
  }
  return best;
}

/**
 * Cargo prints one "test result:" line per test binary (unit, integration, doc
 * tests), so a job's count is their sum.
 */
export function parseCargoSummary(log: string): TestCounts | null {
  let p = 0, f = 0, s = 0, seen = false;
  for (const raw of log.split("\n")) {
    const m = /test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored/.exec(clean(raw));
    if (!m) continue;
    seen = true;
    p += Number(m[1]);
    f += Number(m[2]);
    s += Number(m[3]);
  }
  return seen ? counts(p, f, s) : null;
}

/**
 * Go: gotestsum's "DONE 123 tests, 2 skipped, 1 failure in 3.4s" when present;
 * else `go test -json` events; else the "--- PASS/FAIL/SKIP:" lines of `go test -v`
 * (subtests included). Plain `go test` lists only failing tests, so a log without
 * a sign of -v ("=== RUN") or -json yields null rather than a count of failures.
 */
export function parseGoSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  let p = 0, f = 0, s = 0, verbose = false;
  let jp = 0, jf = 0, js = 0;
  for (const raw of log.split("\n")) {
    const line = clean(raw);
    const done = /DONE (\d+) tests?(?:, (\d+) skipped)?(?:, (\d+) failures?)?(?:, (\d+) errors?)?/.exec(line);
    if (done) {
      const total = Number(done[1]), skipped = Number(done[2] ?? 0), failed = Number(done[3] ?? 0), errors = Number(done[4] ?? 0);
      best = larger(best, counts(Math.max(0, total - skipped - failed), failed, skipped, errors));
      continue;
    }
    const j = /^\s*\{.*"Action":"(pass|fail|skip)".*"Test":"/.exec(line);
    if (j) {
      j[1] === "pass" ? jp++ : j[1] === "fail" ? jf++ : js++;
      continue;
    }
    if (/^\s*=== RUN\s/.test(line)) verbose = true;
    const v = /^\s*--- (PASS|FAIL|SKIP): /.exec(line);
    if (v) v[1] === "PASS" ? p++ : v[1] === "FAIL" ? f++ : s++;
  }
  return best ?? counts(jp, jf, js) ?? (verbose ? counts(p, f, s) : null);
}

/** The largest match of `re` in a log, its counts read by `pick`; for runners that print one summary per run. */
function largestOf(log: string, re: RegExp, pick: (m: RegExpExecArray) => TestCounts | null): TestCounts | null {
  let best: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const m = re.exec(clean(raw));
    if (m) best = larger(best, pick(m));
  }
  return best;
}
const num = (s: string | undefined): number => Number(s ?? 0);

/** RSpec: "1234 examples, 2 failures, 5 pending". */
export const parseRspecSummary = (log: string) =>
  largestOf(log, /(\d+) examples?, (\d+) failures?(?:, (\d+) pending)?/, (m) => counts(num(m[1]) - num(m[2]) - num(m[3]), num(m[2]), num(m[3])));

/** dotnet test: "Passed!  - Failed: 0, Passed: 120, Skipped: 1, Total: 121" (one line per test project, summed). */
export function parseDotnetSummary(log: string): TestCounts | null {
  let sum: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const m = /(?:Passed|Failed)!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+),\s+Skipped:\s+(\d+),\s+Total:\s+(\d+)/.exec(clean(raw));
    if (m) sum = addCounts(sum, counts(num(m[2]), num(m[1]), num(m[3])));
  }
  return sum;
}

/**
 * Maven Surefire/Failsafe: each module's "Results:" line "Tests run: 120, Failures: 1, Errors: 0, Skipped: 2",
 * summed over modules. Per-class lines end in "Time elapsed …" and do not match.
 */
export function parseMavenSummary(log: string): TestCounts | null {
  let sum: TestCounts | null = null;
  for (const raw of log.split("\n")) {
    const m = /^(?:\[\w+\]\s+)?Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)\s*$/.exec(clean(raw));
    if (m) sum = addCounts(sum, counts(num(m[1]) - num(m[2]) - num(m[3]) - num(m[4]), num(m[2]), num(m[4]), num(m[3])));
  }
  return sum;
}

/** PHPUnit / Pest: "OK (123 tests, 456 assertions)" or "Tests: 123, Assertions: 456, Failures: 1, Errors: 0, Skipped: 2." */
export function parsePhpunitSummary(log: string): TestCounts | null {
  return larger(
    largestOf(log, /^OK \((\d+) tests?,/, (m) => counts(num(m[1]), 0, 0)),
    largestOf(log, /^Tests: (\d+), Assertions: \d+(.*)$/, (m) => {
      const n = (w: string) => num(new RegExp(`${w}: (\\d+)`).exec(m[2]!)?.[1]);
      const failed = n("Failures"), errors = n("Errors"), skipped = n("Skipped") + n("Incomplete");
      return counts(num(m[1]) - failed - errors - skipped, failed, skipped, errors);
    }),
  );
}

/** Mocha: "123 passing (4s)", "2 failing", "3 pending" — consecutive lines of one run. */
export function parseMochaSummary(log: string): TestCounts | null {
  let best: TestCounts | null = null;
  const lines = log.split("\n").map(clean);
  for (let i = 0; i < lines.length; i++) {
    const p = /^\s*(\d+) passing \(/.exec(lines[i]!);
    if (!p) continue;
    const near = lines.slice(i + 1, i + 3).join("\n");
    best = larger(best, counts(num(p[1]), num(/(\d+) failing/.exec(near)?.[1]), num(/(\d+) pending/.exec(near)?.[1])));
  }
  return best;
}

export const TEST_PARSERS: Record<typeof TESTS.framework, (log: string) => TestCounts | null> = {
  pytest: parsePytestSummary,
  jest: parseJestSummary,
  vitest: parseVitestSummary,
  cargo: parseCargoSummary,
  go: parseGoSummary,
  rspec: parseRspecSummary,
  dotnet: parseDotnetSummary,
  maven: parseMavenSummary,
  phpunit: parsePhpunitSummary,
  mocha: parseMochaSummary,
};

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
  const url = `https://api.github.com/repos/${PROJECT.repo}/actions/jobs/${jobId}/logs`;
  const res = await githubDownload(env, f, url);
  if (!res.ok) {
    throw new Error(
      `GET ${url} returned HTTP ${res.status}. Job logs need a token with Actions read access and expire after about 90 days.`,
    );
  }
  return res.text();
}

export async function collectTests(
  env: CollectEnv,
  f: Fetcher,
  days: number,
  today: Date,
  skipDays: number,
  refresh = false,
): Promise<string> {
  const workflow = TESTS.workflow;
  const end = utcDay(new Date(today.getTime() - skipDays * 86_400_000));
  const start = utcDay(new Date(today.getTime() - (skipDays + days - 1) * 86_400_000));
  const rows = (
    await env.DB.prepare(
      `SELECT id, created_at FROM workflow_runs WHERE workflow = ? AND ${DEFAULT_BRANCH_BUILD_SQL} AND status = 'completed' AND conclusion IN ('success','failure') AND substr(created_at,1,10) BETWEEN ? AND ?`,
    )
      .bind(workflow, start, end)
      .all<{ id: number; created_at: string }>()
  ).results;
  // The run each stored day came from: a day is re-read whenever a later run has finished since.
  const have = new Map(
    (
      await env.DB.prepare("SELECT day, run_id FROM test_results WHERE day BETWEEN ? AND ?")
        .bind(start, end)
        .all<{ day: string; run_id: number }>()
    ).results.map((r) => [r.day, r.run_id]),
  );
  const rowsOut: unknown[][] = [];
  const failures: string[] = [];
  let read = 0;
  let missing = 0;
  for (const { id, day } of latestRunPerDay(rows)) {
    if (have.get(day) === id && !refresh) continue;
    try {
      const url = `https://api.github.com/repos/${PROJECT.repo}/actions/runs/${id}/jobs?per_page=100`;
      const res = await f(url, { headers: ghHeaders(env) });
      if (!res.ok) throw new Error(`GET ${url} returned HTTP ${res.status}.`);
      const jobs = ((await res.json()) as { jobs: { id: number; conclusion: string | null }[] }).jobs;
      let best: TestCounts | null = null;
      for (const j of jobs) {
        if (j.conclusion !== "success" && j.conclusion !== "failure") continue;
        const c = TEST_PARSERS[TESTS.framework](await jobLog(env, f, j.id));
        // A matrix runs the whole suite per job (take the largest); shards each run a share (add them up).
        best = TESTS.jobs === "sum" ? addCounts(best, c) : larger(best, c);
      }
      if (!best) {
        missing++;
        continue;
      }
      read++;
      rowsOut.push([day, id, workflow, best.passed, best.failed, best.skipped, best.errors, best.total, jobs.length, new Date().toISOString()]);
    } catch (e) {
      // One unreadable day (for example a log past GitHub's retention) must not discard the others.
      failures.push(`${day}: ${(e as Error).message}`);
    }
  }
  await writeAtomic(
    env.DB,
    insertMany(env.DB, "test_results", ["day", "run_id", "workflow", "passed", "failed", "skipped", "errors", "total", "jobs", "collected_at"], rowsOut),
  );
  const summary = `${read} days of test counts from "${workflow}"${missing ? `, ${missing} run(s) had no ${TESTS.framework} summary line` : ""}`;
  if (failures.length) throw new Error(`${summary}; ${failures.length} day(s) could not be read: ${failures.join(" | ")}`);
  // Runs that all lack a summary mean the logs cannot be counted, not that there were no tests.
  if (!read && missing) {
    throw new Error(
      `${summary}. No log of "${workflow}" carries a ${TESTS.framework} summary: check TESTS.framework in src/config.ts` +
        (TESTS.framework === "go" ? ', and run go test with -v or -json (or gotestsum), since plain "go test" lists only failures.' : "."),
    );
  }
  return summary;
}
