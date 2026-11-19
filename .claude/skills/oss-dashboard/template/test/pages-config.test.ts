import { describe, expect, it, vi } from "vitest";

// A project with no registries, no test workflow, no evals, a non-default path and branch, and a hostile name.
vi.mock("../src/config", async (importOriginal) => {
  const real = await importOriginal<typeof import("../src/config")>();
  return {
    ...real,
    PROJECT: { name: 'Acme </script><img src=x onerror=alert(1)> "q"', repo: "acme/widget", defaultBranch: "trunk", basePath: "/dash" },
    PACKAGES: { npm: [], pypi: [], crates: [] },
    TESTS: { workflow: "", framework: "pytest" },
    EVAL_WORKFLOWS: [],
  };
});

const { renderGrowthPage } = await import("../src/growthpage");
const { renderCiPage, renderDevelopersPage, renderUsersPage } = await import("../src/page");
const { SOURCES } = await import("../src/collector");
const worker = (await import("../src/index")).default;

const scripts = (html: string) => [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]!);
const pages: [string, string][] = [
  ["users", renderUsersPage()],
  ["growth", renderGrowthPage([])],
  ["developers", renderDevelopersPage({ people: [], source: "deploy", updatedAt: null, updatedBy: null })],
  ["ci", renderCiPage()],
];

describe("pages for a project with the optional sources off", () => {
  it.each(pages)("%s: every script parses and the name cannot break out of the page", (_n, html) => {
    for (const s of scripts(html)) expect(() => new Function(s)).not.toThrow();
    expect(html).not.toContain("<img src=x");
    expect(html).not.toMatch(/__[A-Z_]+__/);
  });

  it.each(pages)("%s: links and API calls use the configured path, never /admin", (_n, html) => {
    expect(html).not.toMatch(/["'(]\/admin/);
    expect(html).toContain('href="/dash/growth"');
  });

  it("drops the Evals tab, the package card and the tests card", () => {
    const [users, , , ci] = pages.map((p) => p[1]);
    expect(users).not.toContain("/dash/evals");
    expect(users).not.toContain('id="packages"');
    expect(ci).not.toContain('id="tests"');
    expect(ci).toContain("trunk (push, nightly, merge queue)");
  });

  it("collects only the sources the project has", () => {
    expect(SOURCES).not.toContain("package-history");
    expect(SOURCES).not.toContain("tests");
    expect(SOURCES).not.toContain("evals");
  });

  it("serves under the configured path and 404s the old one and the evals page", async () => {
    const env = { DB: {} as D1Database, ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "", ADMIN_DEV_NO_AUTH: "1" };
    expect((await worker.fetch(new Request("http://localhost:8787/admin"), env as never)).status).toBe(404);
    expect((await worker.fetch(new Request("http://localhost:8787/dash/ci"), env as never)).status).toBe(200);
    expect((await worker.fetch(new Request("http://localhost:8787/dash/evals"), env as never)).status).toBe(404);
  });
});
