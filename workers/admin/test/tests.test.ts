// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { jobLog, latestRunPerDay, parsePytestSummary } from "../src/tests";

describe("parsePytestSummary", () => {
  const ts = "2026-10-26T21:43:21.3430526Z ";

  it("reads a framed summary line with GitHub's timestamp prefix", () => {
    expect(parsePytestSummary(`noise\n${ts}===== 1234 passed, 5 skipped in 312.45s (0:05:12) =====\n`)).toEqual({
      passed: 1234, failed: 0, skipped: 5, errors: 0, total: 1239,
    });
  });

  it("reads failures and errors and strips ANSI colour", () => {
    const line = `${ts}\u001b[31m==== 3 failed, 10 passed, 2 errors in 9.1s ====\u001b[0m`;
    expect(parsePytestSummary(line)).toEqual({ passed: 10, failed: 3, skipped: 0, errors: 2, total: 15 });
  });

  it("takes the largest summary when a job runs pytest more than once", () => {
    const log = [
      `${ts}= 28083 passed, 511 skipped, 3 xfailed, 8 warnings, 41 subtests passed in 1256.18s (0:20:56) =`,
      `${ts}======================== 6 passed in 108.48s (0:01:48) ========================`,
    ].join("\n");
    expect(parsePytestSummary(log)).toEqual({ passed: 28083, failed: 0, skipped: 511, errors: 0, total: 28597 });
  });

  it("ignores test names that quote a summary (real names from this repo)", () => {
    const log = [
      `${ts}[gw0] [ 57%] PASSED tests/unit/agents/test_verification_scope.py::test_a_failing_summary_is_a_failed_check_whatever_the_exit_code[========================= 1 failed, 3 passed in 10.03s ======`,
      `${ts}tests/unit/agents/test_answer_quality_checks.py::TestVerifyAfterChangeHelpers::test_test_summaries_recognised[===== 5 passed, 1 skipped in 2.04s =====] `,
      `${ts}tests/unit/agents/test_verification_scope.py::test_python_runner_results_identify_checks[=== 1 failed, 2 passed in 0.03s ===\\n--1-pytest] `,
    ].join("\n");
    expect(parsePytestSummary(log)).toBeNull();
  });

  it("does not count subtests as tests", () => {
    expect(parsePytestSummary("== 5 passed, 41 subtests passed in 1.0s ==")!.passed).toBe(5);
  });

  it("returns null, not zero, when there is no summary", () => {
    expect(parsePytestSummary("Collecting tests...\nno summary here")).toBeNull();
    expect(parsePytestSummary("")).toBeNull();
    expect(parsePytestSummary("== no tests ran in 0.01s ==")).toBeNull();
  });
});

describe("jobLog", () => {
  const env = { DB: {} as D1Database, GITHUB_REPO: "amd/gaia", NPM_PACKAGE: "x", PYPI_PACKAGE: "y", GITHUB_TOKEN: "secret" };

  it("follows GitHub's redirect to storage without sending the token", async () => {
    const calls: { url: string; auth: string | undefined }[] = [];
    const f = async (url: string, init?: RequestInit) => {
      calls.push({ url, auth: (init?.headers as Record<string, string> | undefined)?.Authorization });
      if (new URL(url).host === "api.github.com") return new Response(null, { status: 302, headers: { location: "https://blob.example/log" } });
      return new Response("10 passed in 1.0s");
    };
    expect(await jobLog(env, f, 7)).toBe("10 passed in 1.0s");
    expect(calls).toEqual([
      { url: "https://api.github.com/repos/amd/gaia/actions/jobs/7/logs", auth: "Bearer secret" },
      { url: "https://blob.example/log", auth: undefined },
    ]);
  });

  it("fails with an actionable message when the log is gone", async () => {
    const f = async () => new Response("", { status: 410 });
    await expect(jobLog(env, f, 7)).rejects.toThrow(/expire after about 90 days/);
  });
});

describe("latestRunPerDay", () => {
  it("keeps the newest run of each UTC day, in day order", () => {
    const out = latestRunPerDay([
      { id: 1, created_at: "2026-10-25T08:00:00Z" },
      { id: 2, created_at: "2026-10-25T20:00:00Z" },
      { id: 3, created_at: "2026-10-24T09:00:00Z" },
    ]);
    expect(out).toEqual([{ id: 3, day: "2026-10-24" }, { id: 2, day: "2026-10-25" }]);
  });
});
