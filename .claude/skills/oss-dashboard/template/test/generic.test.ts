import { describe, expect, it } from "vitest";
import { DEFAULT_BRANCH_BUILD_SQL, isDefaultBranchBuild } from "../src/build";
import { collectPackageHistory, collectStars, nextLink, pagedList, STARGAZER_LIST_CAP } from "../src/history";
import { parseCargoSummary, parseDotnetSummary, parseGoSummary, parseJestSummary, parseMavenSummary, parseMochaSummary, parsePhpunitSummary, parsePytestSummary, parseRspecSummary, parseVitestSummary, TEST_PARSERS } from "../src/tests";
import { memoryD1 } from "./d1";

const ok = (body: unknown, headers: Record<string, string> = {}) => new Response(JSON.stringify(body), { status: 200, headers });

describe("nextLink", () => {
  it("returns the next URL, cursor and all, and null on the last page", () => {
    const link =
      '<https://api.github.com/repositories/1/issues?state=all&per_page=100&after=Y3Vyc29y&page=2>; rel="next", <https://api.github.com/repositories/1/issues?page=9>; rel="last"';
    expect(nextLink(link)).toBe("https://api.github.com/repositories/1/issues?state=all&per_page=100&after=Y3Vyc29y&page=2");
    expect(nextLink('<https://x/1>; rel="prev", <https://x/3>; rel="first"')).toBeNull();
    expect(nextLink(null)).toBeNull();
  });
});

describe("pagedList", () => {
  it("follows the Link header instead of building page numbers, and stops when there is no next", async () => {
    const seen: string[] = [];
    const f = async (u: string) => {
      seen.push(u);
      if (u.includes("after=c2")) return ok([{ n: 3 }]);
      return ok([{ n: 1 }, { n: 2 }], { link: '<https://api.github.com/repositories/1/issues?after=c2>; rel="next"' });
    };
    const out = await pagedList(f, { DB: {} as D1Database }, "https://api.github.com/repos/o/r/issues?state=all", "application/json", 10, (x: { n: number }) => x.n);
    expect(out).toEqual([1, 2, 3]);
    expect(seen).toEqual(["https://api.github.com/repos/o/r/issues?state=all&per_page=100", "https://api.github.com/repositories/1/issues?after=c2"]);
  });

  it("fails at the page guard rather than returning a cut-off list", async () => {
    const f = async () => ok([{}], { link: '<https://api.github.com/x?after=again>; rel="next"' });
    await expect(pagedList(f, { DB: {} as D1Database }, "https://api.github.com/x", "a", 3, (x) => x)).rejects.toThrow(/3-page guard/);
  });

  it("explains a stargazer 404 as missing access, not a missing repository", async () => {
    const f = async () => new Response("{}", { status: 404 });
    await expect(pagedList(f, { DB: {} as D1Database }, "https://api.github.com/repos/o/r/stargazers", "a", 3, (x) => x)).rejects.toThrow(/token from one of its maintainers/);
  });
});

describe("collectStars", () => {
  it("refuses a stargazer list cut off at GitHub's cap instead of storing it", async () => {
    let served = 0;
    const f = async () => {
      const batch = Array.from({ length: 100 }, () => ({ starred_at: "2026-01-01T00:00:00Z" }));
      served += 100;
      return ok(batch, served < STARGAZER_LIST_CAP ? { link: '<https://api.github.com/s?after=x>; rel="next"' } : {});
    };
    const db = memoryD1();
    await expect(collectStars({ DB: db }, f)).rejects.toThrow(/STAR_HISTORY = false/);
    expect((await db.prepare("SELECT COUNT(*) AS n FROM star_daily").first<{ n: number }>())!.n).toBe(0);
  });
});

describe("collectPackageHistory npm gaps", () => {
  it("treats npm's uncomputed zero days as gaps for a package that normally has downloads, and removes an old zero row", async () => {
    const db = memoryD1();
    await db.prepare("INSERT INTO package_daily (source, package, day, downloads) VALUES ('npm', 'example', '2026-10-10', 0)").run();
    const days = Array.from({ length: 10 }, (_, i) => ({ day: `2026-10-${String(5 + i).padStart(2, "0")}`, downloads: i === 5 ? 0 : 1000 }));
    const f = async () => ok({ downloads: days });
    await collectPackageHistory({ DB: db }, f, new Date("2026-10-26T12:00:00Z"));
    const rows = (await db.prepare("SELECT day, downloads FROM package_daily ORDER BY day").all<{ day: string; downloads: number }>()).results;
    expect(rows).toHaveLength(9);
    expect(rows.some((r) => r.day === "2026-10-10")).toBe(false);
  });

  it("keeps real zeros for a package that is usually that small", async () => {
    const db = memoryD1();
    const f = async () => ok({ downloads: [{ day: "2026-10-05", downloads: 0 }, { day: "2026-10-06", downloads: 2 }, { day: "2026-10-07", downloads: 0 }] });
    await collectPackageHistory({ DB: db }, f, new Date("2026-10-26T12:00:00Z"));
    expect((await db.prepare("SELECT COUNT(*) AS n FROM package_daily WHERE downloads = 0").first<{ n: number }>())!.n).toBe(2);
  });
});

describe("collectPackageHistory", () => {
  it("stores every configured registry, leaving out the two days registries have not settled", async () => {
    const db = memoryD1();
    const f = async (u: string) => {
      const host = new URL(u).host;
      if (host === "api.npmjs.org")
        return ok({ downloads: [{ day: "2026-10-20", downloads: 4 }, { day: "2026-10-25", downloads: 0 }] });
      throw new Error(`unexpected ${u}`);
    };
    const detail = await collectPackageHistory({ DB: db }, f, new Date("2026-10-26T12:00:00Z"));
    expect(detail).toMatch(/1 daily package rows/);
    const rows = (await db.prepare("SELECT source, package, day, downloads FROM package_daily").all()).results;
    // The example config tracks the npm package "example"; 10-25 is inside the unsettled window.
    expect(rows).toEqual([{ source: "npm", package: "example", day: "2026-10-20", downloads: 4 }]);
  });
});

describe("test-log parsers", () => {
  const ts = (line: string) => `2026-10-01T10:00:00.1234567Z ${line}`;
  it("pytest: the largest framed summary wins", () => {
    const log = [ts("===== 12 passed, 1 skipped in 2.00s ====="), ts("\u001b[32m===== 1200 passed, 3 failed, 5 skipped, 1 error in 312.4s =====\u001b[0m")].join("\n");
    expect(parsePytestSummary(log)).toEqual({ passed: 1200, failed: 3, skipped: 5, errors: 1, total: 1209 });
  });

  it("jest: reads the Tests: line, todo counted with skipped", () => {
    const log = ts("Tests:       1 failed, 2 skipped, 1 todo, 40 passed, 44 total");
    expect(parseJestSummary(log)).toEqual({ passed: 40, failed: 1, skipped: 3, errors: 0, total: 44 });
  });

  it("vitest: reads the Tests line, not Test Files", () => {
    const log = [ts(" Test Files  14 passed (14)"), ts("      Tests  2 failed | 235 passed | 3 skipped (240)")].join("\n");
    expect(parseVitestSummary(log)).toEqual({ passed: 235, failed: 2, skipped: 3, errors: 0, total: 240 });
  });

  it("cargo: sums every test binary", () => {
    const log = [
      ts("test result: ok. 10 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.01s"),
      ts("test result: FAILED. 4 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.20s"),
    ].join("\n");
    expect(parseCargoSummary(log)).toEqual({ passed: 14, failed: 2, skipped: 1, errors: 0, total: 17 });
  });

  it("go: prefers gotestsum's DONE line, else counts -v results including subtests", () => {
    expect(parseGoSummary(ts("DONE 120 tests, 3 skipped, 2 failures in 4.1s"))).toEqual({ passed: 115, failed: 2, skipped: 3, errors: 0, total: 120 });
    const v = [ts("=== RUN   TestA"), ts("--- PASS: TestA (0.00s)"), ts("    --- PASS: TestA/sub (0.00s)"), ts("--- FAIL: TestB (0.01s)"), ts("--- SKIP: TestC (0.00s)")].join("\n");
    expect(parseGoSummary(v)).toEqual({ passed: 2, failed: 1, skipped: 1, errors: 0, total: 4 });
  });

  it("go: counts go test -json events, and refuses plain go test, which lists only failures", () => {
    const json = [
      ts('{"Time":"2026-10-01T10:00:00Z","Action":"run","Package":"x","Test":"TestA"}'),
      ts('{"Time":"2026-10-01T10:00:00Z","Action":"pass","Package":"x","Test":"TestA","Elapsed":0}'),
      ts('{"Time":"2026-10-01T10:00:00Z","Action":"fail","Package":"x","Test":"TestB","Elapsed":0}'),
      ts('{"Time":"2026-10-01T10:00:00Z","Action":"pass","Package":"x","Elapsed":0.1}'),
    ].join("\n");
    expect(parseGoSummary(json)).toEqual({ passed: 1, failed: 1, skipped: 0, errors: 0, total: 2 });
    const plain = [ts("--- FAIL: TestB (0.01s)"), ts("FAIL"), ts("FAIL github.com/x/y 0.2s"), ts("ok   github.com/x/z 0.1s")].join("\n");
    expect(parseGoSummary(plain)).toBeNull();
  });

  it("rspec: examples minus failures and pending", () => {
    expect(parseRspecSummary(ts("1234 examples, 2 failures, 5 pending"))).toEqual({ passed: 1227, failed: 2, skipped: 5, errors: 0, total: 1234 });
    expect(parseRspecSummary(ts("10 examples, 0 failures"))).toEqual({ passed: 10, failed: 0, skipped: 0, errors: 0, total: 10 });
  });

  it("dotnet: sums the per-project result lines", () => {
    const log = [ts("Passed!  - Failed:     0, Passed:   120, Skipped:     1, Total:   121, Duration: 3 s - A.Tests.dll (net8.0)"), ts("Failed!  - Failed:     2, Passed:    40, Skipped:     0, Total:    42, Duration: 1 s - B.Tests.dll (net8.0)")].join("\n");
    expect(parseDotnetSummary(log)).toEqual({ passed: 160, failed: 2, skipped: 1, errors: 0, total: 163 });
  });

  it("maven: sums the module Results lines and ignores per-class lines", () => {
    const log = [
      ts("[INFO] Tests run: 5, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.1 s - in com.example.AppTest"),
      ts("[INFO] Tests run: 50, Failures: 1, Errors: 1, Skipped: 3"),
      ts("[INFO] Tests run: 20, Failures: 0, Errors: 0, Skipped: 0"),
    ].join("\n");
    expect(parseMavenSummary(log)).toEqual({ passed: 65, failed: 1, skipped: 3, errors: 1, total: 70 });
  });

  it("phpunit: both the OK line and the detailed line", () => {
    expect(parsePhpunitSummary(ts("OK (123 tests, 456 assertions)"))).toEqual({ passed: 123, failed: 0, skipped: 0, errors: 0, total: 123 });
    expect(parsePhpunitSummary(ts("Tests: 100, Assertions: 300, Failures: 2, Errors: 1, Skipped: 3."))).toEqual({ passed: 94, failed: 2, skipped: 3, errors: 1, total: 100 });
  });

  it("mocha: passing with the failing and pending lines that follow it", () => {
    const log = [ts("  123 passing (4s)"), ts("  3 pending"), ts("  2 failing")].join("\n");
    expect(parseMochaSummary(log)).toEqual({ passed: 123, failed: 2, skipped: 3, errors: 0, total: 128 });
  });

  it("every parser returns null for a log with no summary, so the day is a gap and not zero", () => {
    const log = ts("npm ci\nbuilding...");
    for (const p of Object.values(TEST_PARSERS)) expect(p(log)).toBeNull();
  });
});

describe("default-branch build runs", () => {
  const run = (event: string, branch: string | null, workflow = "CI") => ({ event, branch, workflow });
  const cases: [ReturnType<typeof run>, boolean][] = [
    [run("push", "main"), true],
    [run("schedule", "main"), true],
    [run("merge_group", "gh-readonly-queue/main/pr-42-abc123"), true],
    [run("merge_group", "gh-readonly-queue/mainline/pr-42-abc123"), false],
    [run("pull_request", "main"), false],
    [run("push", "feature/x"), false],
    [run("workflow_dispatch", "main"), false],
    [run("dynamic", "main", "CodeQL (default setup)"), false],
  ];

  it.each(cases)("%j counts: %s", (r, want) => expect(isDefaultBranchBuild(r)).toBe(want));

  it("the SQL condition selects exactly the runs the JS rule does", async () => {
    const db = memoryD1();
    await db
      .prepare(
        "INSERT INTO workflow_runs (id, workflow, branch, event, status, conclusion, head_sha, run_attempt, created_at, duration_s) SELECT json_extract(value,'$[0]'), json_extract(value,'$[1]'), json_extract(value,'$[2]'), json_extract(value,'$[3]'), 'completed', 'success', 'x', 1, '2026-10-01T00:00:00Z', 60 FROM json_each(?)",
      )
      .bind(JSON.stringify(cases.map(([r], i) => [i + 1, r.workflow, r.branch, r.event])))
      .run();
    const ids = (await db.prepare(`SELECT id FROM workflow_runs WHERE ${DEFAULT_BRANCH_BUILD_SQL} ORDER BY id`).all<{ id: number }>()).results.map((r) => r.id);
    expect(ids).toEqual(cases.flatMap(([, want], i) => (want ? [i + 1] : [])));
  });
});
