// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import {
  cmdDev,
  DEFAULT_PLAYGROUND_CACHE,
  resolveDevCommand,
  resolvePlaygroundPort,
} from "../src/cli.js";

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

describe("dev launcher command resolution", () => {
  it("defaults to the gaia-agent-email console script with serve --reload", () => {
    const { cmd, args } = resolveDevCommand({}, "127.0.0.1", 8131);
    expect(cmd).toBe("gaia-agent-email");
    expect(args).toEqual(["serve", "--reload", "--host", "127.0.0.1", "--port", "8131"]);
  });

  it("uses the module form when --python is given (venv-friendly)", () => {
    const { cmd, args } = resolveDevCommand({ python: "/venv/bin/python" }, "127.0.0.1", 9000);
    expect(cmd).toBe("/venv/bin/python");
    expect(args).toEqual([
      "-m",
      "gaia_agent_email.server",
      "serve",
      "--reload",
      "--host",
      "127.0.0.1",
      "--port",
      "9000",
    ]);
  });

  it("honors a --cmd launcher override", () => {
    const { cmd, args } = resolveDevCommand({ cmd: "/opt/bin/gaia-agent-email" }, "127.0.0.1", 8131);
    expect(cmd).toBe("/opt/bin/gaia-agent-email");
    expect(args[0]).toBe("serve");
  });

  it("--python takes precedence over --cmd", () => {
    const { cmd } = resolveDevCommand(
      { python: "/venv/bin/python", cmd: "/opt/bin/gaia-agent-email" },
      "127.0.0.1",
      8131,
    );
    expect(cmd).toBe("/venv/bin/python");
  });
});

describe("dev fails fast on a bad launcher", () => {
  it("returns 1 quickly (does not hang on the health timeout) when spawn fails", async () => {
    // A launcher that can't be spawned (ENOENT) must fail via the early-exit race,
    // not wait out connectSidecar's 60s health poll. If the abort wiring regressed,
    // this test would hang until the suite timeout.
    const start = Date.now();
    const rc = await cmdDev({
      _: ["dev"],
      flags: { cmd: "amd-gaia-nonexistent-launcher-xyz", port: "8923" },
    });
    expect(rc).toBe(1);
    expect(Date.now() - start).toBeLessThan(10_000);
  });
});

describe("playground binary cache", () => {
  it("defaults the binary cache to a temp dir (not the cwd)", () => {
    expect(DEFAULT_PLAYGROUND_CACHE).toContain("amd-gaia-agent-email");
    expect(DEFAULT_PLAYGROUND_CACHE).not.toBe("amd-gaia-agent-email"); // absolute, under tmpdir
  });
});
