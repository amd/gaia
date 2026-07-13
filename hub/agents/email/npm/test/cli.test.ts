// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import { DEFAULT_PLAYGROUND_CACHE, resolvePlaygroundPort } from "../src/cli.js";

describe("playground --port validation", () => {
  it("defaults to 8131 when no value is given", () => {
    expect(resolvePlaygroundPort(undefined)).toEqual({ port: 8131 });
    // a bare `--port` (no value) parses to boolean true → still the default
    expect(resolvePlaygroundPort(true)).toEqual({ port: 8131 });
  });

  it("accepts a valid port", () => {
    expect(resolvePlaygroundPort("3000")).toEqual({ port: 3000 });
    expect(resolvePlaygroundPort("65535")).toEqual({ port: 65535 });
  });

  it.each(["abc", "0", "-1", "70000", "8131.5", "4001"])(
    "rejects %s with an actionable error (the friendly exit-2 path)",
    (bad) => {
      const r = resolvePlaygroundPort(bad);
      expect(r).toHaveProperty("error");
      expect((r as { error: string }).error).toContain("--port");
    },
  );

  it("explicitly rejects the reserved 4001 (spawnSidecar would RangeError)", () => {
    expect(resolvePlaygroundPort("4001")).toEqual({
      error: expect.stringContaining("4001"),
    });
  });
});

describe("playground binary cache", () => {
  it("defaults the binary cache to a temp dir (not the cwd)", () => {
    expect(DEFAULT_PLAYGROUND_CACHE).toContain("amd-gaia-agent-email");
    expect(DEFAULT_PLAYGROUND_CACHE).not.toBe("amd-gaia-agent-email"); // absolute, under tmpdir
  });
});
