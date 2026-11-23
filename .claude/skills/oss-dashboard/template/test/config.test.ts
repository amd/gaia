import { describe, expect, it } from "vitest";
import * as real from "../src/config";
import * as example from "./example-config";

describe("src/config.ts", () => {
  it("exports every setting the template's tests know about, and no unknown ones", () => {
    // test/example-config.ts is the template's copy; a setting added to one must be added to both.
    expect(Object.keys(real).sort()).toEqual(Object.keys(example).sort());
  });

  it("names a repository and a branch", () => {
    expect(real.PROJECT.repo).toMatch(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/);
    expect(real.PROJECT.defaultBranch).not.toBe("");
  });

  it("serves under a path that matches the route: a leading slash, no trailing one", () => {
    expect(real.PROJECT.basePath).toMatch(/^\/[A-Za-z0-9_\-/]*[A-Za-z0-9_-]$/);
  });

  it("names a test runner whenever a test workflow is set", () => {
    if (real.TESTS.workflow) expect(["pytest", "jest", "vitest", "mocha", "go", "cargo", "rspec", "dotnet", "maven", "phpunit"]).toContain(real.TESTS.framework);
  });

  it("keeps targets ordered: the green floor above the amber one", () => {
    for (const [k, t] of Object.entries(real.TARGETS)) expect(t.good, k).toBeGreaterThanOrEqual(t.warn);
  });
});
