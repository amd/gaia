// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the gaia:// deep-link parser (issue #1725).
 *
 * deep-link.cjs is pure (no Electron), so these run without a running app.
 */

"use strict";

const {
  parseDeepLink,
  extractDeepLinkFromArgv,
} = require("../../src/gaia/apps/webui/services/deep-link.cjs");

describe("parseDeepLink()", () => {
  test("parses a well-formed install link", () => {
    expect(parseDeepLink("gaia://hub/install/summarize")).toEqual({
      action: "install",
      agentId: "summarize",
    });
  });

  test("accepts ids with dots, dashes and underscores", () => {
    expect(parseDeepLink("gaia://hub/install/my-agent_v2.1").agentId).toBe(
      "my-agent_v2.1"
    );
  });

  test("decodes a percent-encoded id", () => {
    expect(parseDeepLink("gaia://hub/install/my%2Dagent").agentId).toBe(
      "my-agent"
    );
  });

  test("rejects a non-gaia scheme", () => {
    expect(() => parseDeepLink("https://hub/install/x")).toThrow(
      /must start with "gaia:\/\/"/
    );
  });

  test("rejects an empty / non-string input", () => {
    expect(() => parseDeepLink("")).toThrow(/Not a GAIA deep link/);
    expect(() => parseDeepLink(null)).toThrow(/Not a GAIA deep link/);
    expect(() => parseDeepLink(undefined)).toThrow(/Not a GAIA deep link/);
  });

  test("rejects an install link with no id", () => {
    expect(() => parseDeepLink("gaia://hub/install/")).toThrow(
      /missing an agent id/
    );
    expect(() => parseDeepLink("gaia://hub/install")).toThrow(
      /missing an agent id/
    );
  });

  test("rejects an id with illegal characters", () => {
    // URL normalization collapses the "../" (popping "install"), so this is
    // rejected as unrecognized rather than reaching id validation — either way
    // it is loudly refused, never silently installing "etc".
    expect(() => parseDeepLink("gaia://hub/install/../etc")).toThrow();
    expect(() => parseDeepLink("gaia://hub/install/a b")).toThrow(
      /invalid agent id/
    );
  });

  test("rejects extra path segments", () => {
    expect(() => parseDeepLink("gaia://hub/install/foo/bar")).toThrow(
      /extra path segments/
    );
  });

  test("rejects an unrecognized host/action", () => {
    expect(() => parseDeepLink("gaia://hub/uninstall/foo")).toThrow(
      /only gaia:\/\/hub\/install/
    );
    expect(() => parseDeepLink("gaia://settings/open")).toThrow(
      /only gaia:\/\/hub\/install/
    );
  });
});

describe("extractDeepLinkFromArgv()", () => {
  test("finds a gaia:// arg among other argv entries", () => {
    const argv = [
      "/path/to/electron",
      "main.cjs",
      "gaia://hub/install/summarize",
    ];
    expect(extractDeepLinkFromArgv(argv)).toBe("gaia://hub/install/summarize");
  });

  test("returns null when no gaia:// arg is present", () => {
    expect(extractDeepLinkFromArgv(["electron", "main.cjs"])).toBeNull();
  });

  test("returns null for a non-array input", () => {
    expect(extractDeepLinkFromArgv(undefined)).toBeNull();
    expect(extractDeepLinkFromArgv(null)).toBeNull();
  });
});
