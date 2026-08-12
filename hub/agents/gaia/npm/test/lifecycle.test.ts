// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Lifecycle: spawn guards, health polling, the version gate, teardown, and the
 * TUI's exit-code propagation. The "sidecar" here is a small Node script so the
 * tests exercise real process management rather than a mock.
 */

import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  BinaryNotFoundError,
  HealthTimeoutError,
  VersionMismatchError,
} from "../src/errors.js";
import {
  API_VERSION,
  RESERVED_PORT,
  checkVersion,
  resolveSidecarPath,
  resolveTuiPath,
  runTui,
  shutdown,
  spawnSidecar,
  startSidecar,
  waitForHealth,
} from "../src/lifecycle.js";

let tmp: string;
const servers: net.Server[] = [];

beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "gaia-lifecycle-"));
});
afterEach(async () => {
  for (const s of servers.splice(0)) await new Promise((r) => s.close(r));
  await fsp.rm(tmp, { recursive: true, force: true });
});

/** A throwaway HTTP server answering /health and /version. */
async function stubSidecar(
  body: { health?: unknown; version?: unknown } = {},
): Promise<string> {
  const http = await import("node:http");
  const server = http.createServer((req, res) => {
    const send = (v: unknown): void => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(v));
    };
    if (req.url === "/health") return send(body.health ?? { status: "ok", service: "gaia-agent-gaia" });
    if (req.url === "/version")
      return send(body.version ?? { apiVersion: API_VERSION, agentVersion: "0.1.0" });
    res.writeHead(404).end();
  });
  servers.push(server);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const addr = server.address() as net.AddressInfo;
  return `http://127.0.0.1:${addr.port}`;
}

/** Write a Node script and return its path (not itself spawnable). */
async function script(name: string, js: string): Promise<string> {
  const p = path.join(tmp, `${name}.mjs`);
  await fsp.writeFile(p, js);
  return p;
}

/**
 * A directly-spawnable surrogate for the frozen sidecar: a shebang wrapper that
 * forwards `--host`/`--port` to a Node script.
 *
 * POSIX only. Node refuses to `spawn()` a `.cmd`/`.bat` without `shell: true`
 * (the CVE-2024-27980 hardening) and there is no way to make `node.exe` itself
 * take a script path *after* the `--host`/`--port` that `spawnSidecar` puts
 * first. The real artifact is a PyInstaller `.exe`, so this gap is in the test
 * surrogate, not the code under test — the Windows paths of `spawnSidecar` /
 * `shutdown` (taskkill) are exercised in CI on a POSIX-equivalent path and by
 * the guard tests above, which need no spawn at all.
 */
async function fakeBinary(name: string, js: string): Promise<string> {
  const target = await script(name, js);
  const sh = path.join(tmp, name);
  await fsp.writeFile(sh, `#!/bin/sh\nexec node "${target}" "$@"\n`, { mode: 0o755 });
  return sh;
}

/** Skips the suites that need a spawnable sidecar surrogate (see fakeBinary). */
const posixOnly = process.platform === "win32" ? describe.skip : describe;

describe("spawnSidecar guards", () => {
  it("refuses the reserved port 4001 before spawning anything", async () => {
    // Any existing file will do: the port guard must fire before the spawn.
    const bin = await script("noop", "process.exit(0)");
    expect(() => spawnSidecar({ binaryPath: bin, port: RESERVED_PORT })).toThrow(
      RangeError,
    );
  });

  it("throws BinaryNotFoundError for a path that does not exist", () => {
    expect(() => spawnSidecar({ binaryPath: path.join(tmp, "absent") })).toThrow(
      BinaryNotFoundError,
    );
  });

  it("requires a binaryPath", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(() => spawnSidecar({} as any)).toThrow(TypeError);
  });
});

describe("waitForHealth", () => {
  it("returns once the server reports ok", async () => {
    const baseUrl = await stubSidecar();
    await expect(waitForHealth(baseUrl, { timeoutMs: 5000 })).resolves.toBeUndefined();
  });

  it("throws HealthTimeoutError naming the url and the timeout", async () => {
    const p = waitForHealth("http://127.0.0.1:1", { timeoutMs: 300, intervalMs: 50 });
    await expect(p).rejects.toBeInstanceOf(HealthTimeoutError);
    await expect(p).rejects.toThrow(/did not become healthy within 300ms/);
  });

  it("aborts immediately when its signal fires instead of running out the clock", async () => {
    const ac = new AbortController();
    ac.abort();
    const started = Date.now();
    await expect(
      waitForHealth("http://127.0.0.1:1", { timeoutMs: 30_000, signal: ac.signal }),
    ).rejects.toThrow(/aborted/);
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it("keeps polling while the server reports a non-ok status", async () => {
    const baseUrl = await stubSidecar({ health: { status: "starting" } });
    await expect(
      waitForHealth(baseUrl, { timeoutMs: 300, intervalMs: 50 }),
    ).rejects.toBeInstanceOf(HealthTimeoutError);
  });
});

describe("checkVersion", () => {
  it("accepts the exact contract version", async () => {
    const baseUrl = await stubSidecar();
    await expect(checkVersion(baseUrl)).resolves.toMatchObject({
      apiVersion: API_VERSION,
    });
  });

  it("accepts a HIGHER minor with the same major (additive change)", async () => {
    const baseUrl = await stubSidecar({
      version: { apiVersion: "2.99", agentVersion: "0.9.0" },
    });
    await expect(checkVersion(baseUrl)).resolves.toMatchObject({ apiVersion: "2.99" });
  });

  it("REFUSES a differing major (breaking change)", async () => {
    const baseUrl = await stubSidecar({
      version: { apiVersion: "3.0", agentVersion: "1.0.0" },
    });
    const p = checkVersion(baseUrl);
    await expect(p).rejects.toBeInstanceOf(VersionMismatchError);
    await expect(p).rejects.toThrow(/major/);
  });

  it("refuses an unparseable apiVersion rather than assuming compatibility", async () => {
    const baseUrl = await stubSidecar({
      version: { apiVersion: "unknown", agentVersion: "0.1.0" },
    });
    await expect(checkVersion(baseUrl)).rejects.toBeInstanceOf(VersionMismatchError);
  });
});

posixOnly("shutdown", () => {
  it("is a no-op on an already-exited process", async () => {
    const bin = await fakeBinary("quick", "process.exit(0)");
    const sidecar = spawnSidecar({ binaryPath: bin, port: 8199, autoCleanup: false });
    await new Promise<void>((r) => sidecar.child.once("exit", () => r()));
    await expect(shutdown(sidecar)).resolves.toBeUndefined();
  });

  it("terminates a long-running sidecar and resolves", async () => {
    const bin = await fakeBinary("sleeper", "setInterval(() => {}, 1000)");
    const sidecar = spawnSidecar({ binaryPath: bin, port: 8198, autoCleanup: false });
    await shutdown(sidecar, 10_000);
    expect(sidecar.child.exitCode !== null || sidecar.child.signalCode !== null).toBe(true);
  }, 20_000);
});

posixOnly("startSidecar", () => {
  it("gives up as soon as the process dies instead of waiting out the health timeout", async () => {
    const bin = await fakeBinary("dies", "process.exit(3)");
    const started = Date.now();
    await expect(
      startSidecar({
        binaryPath: bin,
        port: 8197,
        autoCleanup: false,
        healthTimeoutMs: 30_000,
      }),
    ).rejects.toBeInstanceOf(HealthTimeoutError);
    // Without the exit-abort wiring this would sit for the full 30s.
    expect(Date.now() - started).toBeLessThan(15_000);
  }, 40_000);
});

describe("runTui", () => {
  // runTui passes args through verbatim, so the real `node` executable is a
  // valid stand-in on every platform (unlike the sidecar surrogate above).
  const node = process.execPath;

  it("propagates a non-zero exit code verbatim", async () => {
    const s = await script("exit7", "process.exit(7)");
    await expect(runTui({ binaryPath: node, args: [s] })).resolves.toBe(7);
  });

  it("propagates a clean exit", async () => {
    const s = await script("exit0", "process.exit(0)");
    await expect(runTui({ binaryPath: node, args: [s] })).resolves.toBe(0);
  });

  it("forwards args verbatim", async () => {
    const s = await script("argc", "process.exit(process.argv.slice(2).length)");
    await expect(
      runTui({ binaryPath: node, args: [s, "--debug", "chat"] }),
    ).resolves.toBe(2);
  });

  it("passes the caller's env through to the child", async () => {
    const s = await script("env", "process.exit(process.env.GAIA_TEST_MARKER === 'yes' ? 5 : 1)");
    await expect(
      runTui({ binaryPath: node, args: [s], env: { GAIA_TEST_MARKER: "yes" } }),
    ).resolves.toBe(5);
  });

  it("throws BinaryNotFoundError for a missing binary", () => {
    expect(() => runTui({ binaryPath: path.join(tmp, "absent") })).toThrow(
      BinaryNotFoundError,
    );
  });
});

describe("resolve*Path", () => {
  it("throws an actionable BinaryNotFoundError when nothing is fetched yet", () => {
    for (const fn of [resolveSidecarPath, resolveTuiPath]) {
      try {
        fn({ resourcesDir: tmp });
        throw new Error("should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(BinaryNotFoundError);
        expect((e as Error).message).toContain("npx @amd-gaia/gaia fetch");
      }
    }
  });

  it("returns the path once the binary is present", async () => {
    const exe = process.platform === "win32" ? "gaia-tui.exe" : "gaia-tui";
    fs.writeFileSync(path.join(tmp, exe), "x");
    expect(resolveTuiPath({ resourcesDir: tmp })).toBe(path.join(tmp, exe));
  });
});
