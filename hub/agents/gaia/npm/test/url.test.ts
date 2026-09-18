// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * URL joining. The slash trimmers were rewritten as linear char scans to kill a
 * polynomial-ReDoS finding (CodeQL js/polynomial-redos) and had no test at all,
 * so a regression back to `/\/+$/` would have gone unnoticed.
 */

import { describe, expect, it } from "vitest";

import { joinUrl, stripLeadingSlashes, stripTrailingSlashes } from "../src/url.js";

describe("stripTrailingSlashes", () => {
  it("removes every trailing slash and nothing else", () => {
    expect(stripTrailingSlashes("https://h/a/")).toBe("https://h/a");
    expect(stripTrailingSlashes("https://h/a///")).toBe("https://h/a");
    expect(stripTrailingSlashes("https://h/a")).toBe("https://h/a");
  });

  it("leaves interior slashes alone", () => {
    expect(stripTrailingSlashes("https://h/a/b")).toBe("https://h/a/b");
  });

  it("collapses an all-slash string to empty", () => {
    expect(stripTrailingSlashes("////")).toBe("");
    expect(stripTrailingSlashes("")).toBe("");
  });
});

describe("stripLeadingSlashes", () => {
  it("removes every leading slash and nothing else", () => {
    expect(stripLeadingSlashes("/a")).toBe("a");
    expect(stripLeadingSlashes("///a")).toBe("a");
    expect(stripLeadingSlashes("a")).toBe("a");
  });

  it("collapses an all-slash string to empty", () => {
    expect(stripLeadingSlashes("////")).toBe("");
  });
});

describe("joinUrl", () => {
  it("joins with exactly one slash regardless of the input's slashes", () => {
    for (const base of ["https://h/v1", "https://h/v1/", "https://h/v1///"]) {
      for (const file of ["gaia-agent", "/gaia-agent", "///gaia-agent"]) {
        expect(joinUrl(base, file)).toBe("https://h/v1/gaia-agent");
      }
    }
  });

  it("stays linear on a long run of slashes (the ReDoS case)", () => {
    // The regex forms backtracked polynomially here; a char scan is O(n).
    const nasty = `https://h/${"/".repeat(50_000)}`;
    const started = Date.now();
    expect(joinUrl(nasty, "x")).toBe("https://h/x");
    expect(Date.now() - started).toBeLessThan(1000);
  });
});
