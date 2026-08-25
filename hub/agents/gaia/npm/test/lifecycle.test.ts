// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Lifecycle: spawn guards, health polling, the version gate, teardown, and the
 * TUI's exit-code propagation. The "sidecar" here is a small Node script so the
 * tests exercise real process management rather than a mock.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  BinaryNotFoundError,
  GaiaError,
  HealthTimeoutError,
  IntegrityError,
  MalformedResponseError,
  SidecarExitedError,
  VersionMismatchError,
} from "../src/errors.js";
import {
  API_VERSION,
  RESERVED_PORT,
  checkVersion,
  health,
  resolveSidecarPath,
  resolveTuiPath,
  runTui,
  shutdown,
  spawnSidecar,
  startSidecar,
  tuiExecutableName,
  waitForHealth,
} from "../src/lifecycle.js";
import { makeLock } from "./helpers.js";

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

/**
 * A stub sidecar on a known port, with per-route delays so a race can be pinned
 * down deterministically. Returns the port so a spawn can be aimed at it.
 */
async function stubSidecarOnPort(
  opts: { versionDelayMs?: number; healthBody?: unknown } = {},
): Promise<{ baseUrl: string; port: number }> {
  const http = await import("node:http");
  const server = http.createServer((req, res) => {
    const send = (v: unknown, delayMs = 0): void => {
      setTimeout(() => {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(v));
      }, delayMs);
    };
    if (req.url === "/health")
      return send(opts.healthBody ?? { status: "ok", service: "not-ours" });
    if (req.url === "/version")
      return send({ apiVersion: API_VERSION, agentVersion: "9.9.9" }, opts.versionDelayMs ?? 0);
    res.writeHead(404).end();
  });
  servers.push(server);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const { port } = server.address() as net.AddressInfo;
  return { baseUrl: `http://127.0.0.1:${port}`, port };
}

/** An HTTP server that answers 200 with a body that is NOT JSON. */
async function stubHtml(): Promise<string> {
  const http = await import("node:http");
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html" });
    res.end("<html><body>502 Bad Gateway (corporate proxy)</body></html>");
  });
  servers.push(server);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  return `http://127.0.0.1:${(server.address() as net.AddressInfo).port}`;
}

/**
 * A binary that is guaranteed to die instantly on every platform: `node` rejects
 * the `--host`/`--port` that spawnSidecar puts first ("bad option") and exits 9
 * in ~25ms. Unlike `fakeBinary` this needs no shell wrapper, so it runs on
 * Windows too.
 */
const diesInstantly = (): string => process.execPath;

const sleepFor = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

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

describe("startSidecar vs a FOREIGN server already on the port", () => {
  it("refuses a handle whose own child is dead, naming the port", async () => {
    // The failure this reproduces: a second `gaia serve` finds 8141 already
    // bound, its own sidecar dies, but the incumbent answers /health and
    // /version — so startSidecar used to hand back a handle it did not own, and
    // shutdown then logged "already exited" while the real server kept running.
    const { port } = await stubSidecarOnPort({ versionDelayMs: 750 });
    const p = startSidecar({
      binaryPath: diesInstantly(),
      port,
      autoCleanup: false,
      healthTimeoutMs: 20_000,
    });
    await expect(p).rejects.toBeInstanceOf(SidecarExitedError);
    await expect(p).rejects.toThrow(new RegExp(`port ${port}`));
    await expect(p).rejects.toThrow(/another process is already bound/);
  }, 30_000);

  it("reports a SidecarExitedError as a GaiaError, so the CLI formats it", async () => {
    const { port } = await stubSidecarOnPort({ versionDelayMs: 750 });
    await expect(
      startSidecar({
        binaryPath: diesInstantly(),
        port,
        autoCleanup: false,
        healthTimeoutMs: 20_000,
      }),
    ).rejects.toBeInstanceOf(GaiaError);
  }, 30_000);
});

describe("non-JSON responses", () => {
  it("raises a GaiaError, not a bare SyntaxError, for a 200 that is HTML", async () => {
    // A proxy answering 200 with an error page used to escape the CLI's
    // `instanceof GaiaError` branch and print a raw stack.
    const baseUrl = await stubHtml();
    const p = health(baseUrl);
    await expect(p).rejects.toBeInstanceOf(MalformedResponseError);
    await expect(p).rejects.toBeInstanceOf(GaiaError);
    await expect(p).rejects.toThrow(/not JSON/);
  });

  it("keeps polling rather than crashing when health is not JSON", async () => {
    const baseUrl = await stubHtml();
    await expect(
      waitForHealth(baseUrl, { timeoutMs: 300, intervalMs: 50 }),
    ).rejects.toBeInstanceOf(HealthTimeoutError);
  });
});

describe("auto-cleanup registration", () => {
  it("installs the process-level reapers by default", () => {
    // autoCleanup defaults true, but every spawning test passed false, so none
    // of installCleanupHandlers/registerForCleanup ever ran under test.
    const sidecar = spawnSidecar({ binaryPath: diesInstantly(), port: 8195 });
    try {
      expect(process.listenerCount("exit")).toBeGreaterThan(0);
      for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"] as const) {
        expect(process.listenerCount(sig)).toBeGreaterThan(0);
      }
    } finally {
      sidecar.child.kill("SIGKILL");
    }
  });
});

posixOnly("auto-cleanup ownership", () => {
  /**
   * A live sidecar registered for auto-cleanup, torn down whatever happens.
   * Needs a surrogate that ignores --host/--port and stays up, which on Windows
   * would require a real .exe fixture.
   */
  async function withLiveSidecar(
    port: number,
    body: (s: ReturnType<typeof spawnSidecar>) => Promise<void>,
  ): Promise<void> {
    const bin = await fakeBinary(`live-${port}`, "setInterval(() => {}, 1000)");
    const sidecar = spawnSidecar({ binaryPath: bin, port });
    try {
      await body(sidecar);
    } finally {
      await shutdown(sidecar, 10_000).catch(() => undefined);
    }
  }

  it("does NOT reap the sidecar when the HOST app owns the signal", async () => {
    // Importing this library must not change what a host's own SIGINT handler
    // means. It used to: the reap ran before the ownership check, so a
    // host-handled signal killed the sidecar and its next request got an
    // unexplained ECONNREFUSED.
    await withLiveSidecar(8194, async (sidecar) => {
      const hostHandler = (): void => {};
      process.on("SIGINT", hostHandler);
      try {
        process.emit("SIGINT");
        await sleepFor(400);
      } finally {
        process.removeListener("SIGINT", hostHandler);
      }
      expect(sidecar.child.exitCode).toBeNull();
      expect(sidecar.child.signalCode).toBeNull();
    });
  }, 30_000);

  it("de-registers only once the child has actually exited", async () => {
    // shutdown() used to de-register up front, so a sidecar that survived both
    // kill windows became a permanent orphan: it threw correctly but was gone
    // from the set the process-exit backstop reaps. The survivor branch itself
    // cannot be tested — nothing can ignore SIGKILL — so this pins the other
    // half: a COMPLETED shutdown does de-register, and the child really is gone.
    await withLiveSidecar(8193, async (sidecar) => {
      await shutdown(sidecar, 10_000);
      expect(sidecar.child.exitCode !== null || sidecar.child.signalCode !== null).toBe(
        true,
      );
    });
  }, 30_000);
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
  const exe = (): string => tuiExecutableName();
  /** Write a TUI binary and a lock that agrees (or not) with its bytes. */
  function stage(content: string, lockContent = content): { full: string; lock: ReturnType<typeof makeLock> } {
    const full = path.join(tmp, exe());
    fs.writeFileSync(full, content);
    const sha = crypto.createHash("sha256").update(lockContent).digest("hex");
    return { full, lock: makeLock(sha) };
  }

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

  it("returns the path once the binary is present and matches the lock", () => {
    const { full, lock } = stage("a verified gaia-tui");
    expect(resolveTuiPath({ resourcesDir: tmp, lock })).toBe(full);
  });

  it("REFUSES a binary whose bytes do not match the lock", () => {
    // fetch.ts calls the SHA verify "the security boundary". This path is
    // exported and its cache dir is predictable, so anything able to write
    // ~/.gaia/agents/gaia used to get code spawned unverified.
    const { full, lock } = stage("tampered", "what the lock pins");
    try {
      resolveTuiPath({ resourcesDir: tmp, lock });
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(IntegrityError);
      expect((e as Error).message).toContain(full);
      expect((e as Error).message).toContain("Refusing to spawn");
    }
  });

  it("skips the check only when the caller opts out explicitly", () => {
    const { full, lock } = stage("a self-built binary", "something else");
    expect(resolveTuiPath({ resourcesDir: tmp, lock, verify: false })).toBe(full);
  });

  it("refuses a placeholder-hash lock rather than treating it as verified", () => {
    const { lock } = stage("anything");
    for (const e of Object.values(lock.components["tui"]!.platforms)) e.sha256 = "0".repeat(64);
    expect(() => resolveTuiPath({ resourcesDir: tmp, lock })).toThrow(IntegrityError);
  });
});
