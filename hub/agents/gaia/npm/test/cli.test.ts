// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Argument parsing, port validation, and the lifecycle layer's name contracts. */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { PlatformError } from "../src/errors.js";

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

  it("refuses anything Number() would coerce behind the user's back", () => {
    // `Number("0x2710")` is 10000 — a typo would have bound a port never named.
    for (const bad of ["0x2710", " 80 ", "1e3", "+80", "80\n", "0b1010", ""]) {
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
  let tmp: string;
  const resolveAll = (p: string): string[] => p.split(sep).map((d) => path.resolve(d));

  beforeEach(async () => {
    tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "gaia-shim-"));
  });
  afterEach(async () => {
    await fsp.rm(tmp, { recursive: true, force: true });
  });

  /** A bin dir containing exactly `names`, plus the path of its `gaia` entry. */
  function binDir(name: string, names: string[]): { dir: string; argv1: string } {
    const dir = path.join(tmp, name);
    fs.mkdirSync(dir, { recursive: true });
    for (const n of names) fs.writeFileSync(path.join(dir, n), "");
    return { dir, argv1: path.join(dir, "gaia") };
  }

  it("drops a dir holding nothing but our own shim (an npx temp dir)", () => {
    // The TUI starts the daemon by resolving `gaia` on PATH, expecting the
    // PYTHON CLI. Our npm shim has the same name; left on PATH it would win and
    // the TUI would re-invoke us with `daemon start`.
    const { dir, argv1 } = binDir("npx-cache", ["gaia", "gaia.cmd", "gaia.ps1"]);
    const raw = [dir, path.resolve("/usr/local/bin"), path.resolve("/usr/bin")].join(sep);
    const out = resolveAll(pathWithoutOwnShim(raw, argv1)!);
    expect(out).not.toContain(path.resolve(dir));
    expect(out).toContain(path.resolve("/usr/local/bin"));
    expect(out).toContain(path.resolve("/usr/bin"));
  });

  it("KEEPS a shared bin dir, so its other executables stay reachable", () => {
    // Homebrew/pipx layout: our shim shares /opt/homebrew/bin (or ~/.local/bin)
    // with python3, git, and lemonade-server. Dropping the directory took those
    // off the child's PATH and the TUI then reported failures we had caused.
    const { dir, argv1 } = binDir("shared-bin", ["gaia", "python3", "lemonade-server"]);
    const raw = [dir, path.resolve("/usr/bin")].join(sep);
    const out = resolveAll(pathWithoutOwnShim(raw, argv1)!);
    expect(out).toContain(path.resolve(dir));
    expect(out).toContain(path.resolve("/usr/bin"));
  });

  it("moves a shared bin dir LAST, so any other `gaia` on PATH wins", () => {
    const { dir, argv1 } = binDir("shared-order", ["gaia", "python3"]);
    const realCli = path.resolve("/opt/venv/bin");
    const out = resolveAll(pathWithoutOwnShim([dir, realCli].join(sep), argv1)!);
    expect(out.indexOf(realCli)).toBeLessThan(out.indexOf(path.resolve(dir)));
    expect(out[out.length - 1]).toBe(path.resolve(dir));
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

describe("value flags a command would ignore", () => {
  // Parsing a flag the command never reads is worse than refusing it: the user
  // asked for something and got the default without being told.
  const IGNORED: ReadonlyArray<[string, string[]]> = [
    ["--port", ["run", "--port", "9000"]],
    ["--port", ["fetch", "--port", "9000"]],
    ["--component", ["run", "--component", "tui"]],
    ["--component", ["serve", "--component", "tui"]],
    ["--cache-dir", ["serve", "--cache-dir", "/tmp/x"]],
    ["--base-url", ["version", "--base-url", "https://mirror.test/x"]],
  ];

  for (const [flag, argv] of IGNORED) {
    it(`refuses ${flag} for \`gaia ${argv[0]}\` instead of dropping it`, async () => {
      const p = main(argv);
      await expect(p).rejects.toBeInstanceOf(UsageError);
      await expect(p).rejects.toThrow(new RegExp(`${flag} is not valid`));
      // The message must say where the flag DOES work, not just that it failed.
      await expect(p).rejects.toThrow(/accepted by/);
    });
  }

  it("still accepts each flag on the command that reads it", () => {
    // Parse-level only; the commands themselves need the network.
    expect(parseArgs(["serve", "--port", "9000"]).flags["port"]).toBe("9000");
    expect(parseArgs(["fetch", "--component", "tui"]).flags["component"]).toBe("tui");
  });
});

describe("--base-url scheme", () => {
  it("REFUSES a plaintext base URL, naming the opt-out", async () => {
    const p = main(["fetch", "--base-url", "http://mirror.internal/gaia"]);
    await expect(p).rejects.toBeInstanceOf(UsageError);
    await expect(p).rejects.toThrow(/https/);
    await expect(p).rejects.toThrow(/--allow-insecure-base-url/);
  });

  it("refuses a value that is not a URL at all", async () => {
    await expect(main(["fetch", "--base-url", "mirror.internal/gaia"])).rejects.toThrow(
      /not a valid absolute URL/,
    );
  });

  it("allows plaintext once the opt-out is explicit", async () => {
    // Gets past the scheme gate and fails later, on the platform lookup — which
    // is a PlatformError, not a UsageError, and needs no network.
    const p = main([
      "fetch",
      "--base-url",
      "http://mirror.internal/gaia",
      "--allow-insecure-base-url",
      "--component",
      "sidecar",
      "--platform",
      "linux-arm64",
    ]);
    await expect(p).rejects.toBeInstanceOf(PlatformError);
    await expect(p).rejects.toThrow(/linux-arm64/);
  });

  it("accepts https without the opt-out", async () => {
    const p = main([
      "fetch",
      "--base-url",
      "https://mirror.internal/gaia",
      "--component",
      "sidecar",
      "--platform",
      "linux-arm64",
    ]);
    await expect(p).rejects.toBeInstanceOf(PlatformError);
  });
});
