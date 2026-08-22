// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Argument parsing, port validation, and the lifecycle layer's name contracts. */

import { describe, expect, it } from "vitest";

import path from "node:path";

import { UsageError, main, parseArgs, pathWithoutOwnShim, resolvePort } from "../src/cli.js";
import {
  API_VERSION,
  DEFAULT_PORT,
  RESERVED_PORT,
  sidecarExecutableName,
  tuiExecutableName,
} from "../src/lifecycle.js";

describe("parseArgs", () => {
  it("defaults to no command and no flags", () => {
    expect(parseArgs([])).toEqual({ _: [], flags: {}, passthrough: [] });
  });

  it("reads value flags and boolean switches", () => {
    const a = parseArgs(["fetch", "--platform", "linux-x64", "--force"]);
    expect(a._).toEqual(["fetch"]);
    expect(a.flags["platform"]).toBe("linux-x64");
    expect(a.flags["force"]).toBe(true);
  });

  it("reads the --flag=value form", () => {
    // Dropping this silently would download from the default hub after the user
    // explicitly pointed us at a mirror.
    const a = parseArgs(["fetch", "--base-url=https://mirror.test/x", "--component=tui"]);
    expect(a.flags["base-url"]).toBe("https://mirror.test/x");
    expect(a.flags["component"]).toBe("tui");
  });

  it("REFUSES a value flag with no value instead of continuing", () => {
    expect(() => parseArgs(["fetch", "--base-url", "--force"])).toThrow(UsageError);
    expect(() => parseArgs(["fetch", "--base-url="])).toThrow(UsageError);
  });

  it("refuses a value handed to a boolean switch", () => {
    expect(() => parseArgs(["run", "--force=yes"])).toThrow(UsageError);
  });

  it("forwards everything after `--` to the TUI verbatim", () => {
    const a = parseArgs(["run", "--force", "--", "--debug", "chat", "--model", "x"]);
    expect(a.flags["force"]).toBe(true);
    expect(a.passthrough).toEqual(["--debug", "chat", "--model", "x"]);
  });

  it("treats -h as --help", () => {
    expect(parseArgs(["-h"]).flags["help"]).toBe(true);
  });
});

describe("resolvePort", () => {
  it("defaults to the sidecar's DEFAULT_PORT", () => {
    expect(resolvePort(undefined)).toEqual({ port: DEFAULT_PORT });
    expect(DEFAULT_PORT).toBe(8141);
  });

  it("accepts a valid port", () => {
    expect(resolvePort("9000")).toEqual({ port: 9000 });
  });

  it("REFUSES the reserved port 4001", () => {
    expect(RESERVED_PORT).toBe(4001);
    const r = resolvePort("4001");
    expect(r).toHaveProperty("error");
    expect((r as { error: string }).error).toContain("4001");
  });

  it("refuses out-of-range and non-numeric ports", () => {
    for (const bad of ["0", "65536", "-1", "http", "80.5"]) {
      expect(resolvePort(bad)).toHaveProperty("error");
    }
  });
});

describe("executable names", () => {
  it("names the sidecar gaia-agent per platform", () => {
    expect(sidecarExecutableName("linux")).toBe("gaia-agent");
    expect(sidecarExecutableName("darwin")).toBe("gaia-agent");
    expect(sidecarExecutableName("win32")).toBe("gaia-agent.exe");
  });

  it("names the TUI gaia-tui — never `gaia`, which is the npm bin shim", () => {
    expect(tuiExecutableName("linux")).toBe("gaia-tui");
    expect(tuiExecutableName("win32")).toBe("gaia-tui.exe");
    expect(tuiExecutableName("linux")).not.toBe("gaia");
  });
});

describe("contract constants", () => {
  it("matches the sidecar's API_VERSION", () => {
    // gaia_agent_gaia/server.py: API_VERSION = "2.12"
    expect(API_VERSION).toBe("2.12");
  });
});

describe("pathWithoutOwnShim", () => {
  const sep = process.platform === "win32" ? ";" : ":";

  it("removes the directory holding our own `gaia` shim", () => {
    // The TUI starts the daemon by resolving `gaia` on PATH, expecting the
    // PYTHON CLI. Our npm shim has the same name; left on PATH it would win and
    // the TUI would re-invoke us with `daemon start`.
    const shimDir = path.resolve("/tmp/npm/bin");
    const argv1 = path.join(shimDir, "gaia");
    const raw = [shimDir, path.resolve("/usr/local/bin"), path.resolve("/usr/bin")].join(sep);
    const out = pathWithoutOwnShim(raw, argv1)!.split(sep).map((d) => path.resolve(d));
    expect(out).not.toContain(shimDir);
    expect(out).toContain(path.resolve("/usr/local/bin"));
    expect(out).toContain(path.resolve("/usr/bin"));
  });

  it("leaves a PATH that does not contain our shim dir untouched", () => {
    const raw = [path.resolve("/usr/local/bin"), path.resolve("/usr/bin")].join(sep);
    const argv1 = path.resolve("/elsewhere/bin/gaia");
    expect(pathWithoutOwnShim(raw, argv1)!.split(sep).length).toBe(2);
  });

  it("survives an unset PATH or an unknown argv[1]", () => {
    // PATH has to be unset in the ENVIRONMENT: rawPath defaults to
    // process.env.PATH, so passing `undefined` alone re-reads the real PATH.
    const saved = process.env["PATH"];
    try {
      delete process.env["PATH"];
      expect(pathWithoutOwnShim(undefined, "/x/gaia")).toBeUndefined();
    } finally {
      if (saved === undefined) delete process.env["PATH"];
      else process.env["PATH"] = saved;
    }
    expect(pathWithoutOwnShim("/usr/bin", "")).toBe("/usr/bin");
  });
});

describe("main dispatch", () => {
  it("refuses an unknown command", async () => {
    await expect(main(["bogus"])).rejects.toBeInstanceOf(UsageError);
  });

  it("refuses a stray positional rather than silently dropping it", async () => {
    await expect(main(["run", "chat"])).rejects.toThrow(/unexpected argument/);
  });

  it("refuses --platform for run and serve, which execute what they download", async () => {
    for (const cmd of ["run", "serve"]) {
      await expect(main([cmd, "--platform", "darwin-x64"])).rejects.toThrow(/--platform/);
    }
  });

  it("refuses an unknown --component", async () => {
    await expect(main(["fetch", "--component", "installer"])).rejects.toThrow(
      /unknown --component/,
    );
  });

  it("prints help for --help and for the help command", async () => {
    for (const argv of [["--help"], ["-h"], ["help"], ["fetch", "--help"]]) {
      await expect(main(argv)).resolves.toBe(0);
    }
  });
});
