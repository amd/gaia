// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * startSidecar must never hand back a handle for a server it did not start, and
 * must not sit out the full health timeout when its own child dies. The
 * "sidecar" is a real process and the incumbent a real listener, so these
 * exercise actual port ownership rather than a mock.
 */

import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  AgentEmailError,
  BinaryNotFoundError,
  HealthTimeoutError,
  PortInUseError,
  SidecarExitedError,
} from "../src/errors.js";
import { startSidecar } from "../src/lifecycle.js";
import { SCHEMA_VERSION } from "../src/types.js";

let tmp: string;
const servers: net.Server[] = [];

beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "email-lifecycle-"));
});
afterEach(async () => {
  for (const s of servers.splice(0)) await new Promise((r) => s.close(r));
  await fsp.rm(tmp, { recursive: true, force: true });
});

/** A foreign server holding a port and answering /health and /version, as a sidecar would. */
async function foreignServer(): Promise<number> {
  const server = http.createServer((req, res) => {
    const send = (v: unknown): void => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(v));
    };
    if (req.url === "/health") return send({ status: "ok", service: "not-ours" });
    if (req.url === "/version") return send({ apiVersion: SCHEMA_VERSION, agentVersion: "9.9.9" });
    res.writeHead(404).end();
  });
  servers.push(server);
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  return (server.address() as net.AddressInfo).port;
}

/** A port that is free right now — bound, read, and released. */
async function freePort(): Promise<number> {
  const server = net.createServer();
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const { port } = server.address() as net.AddressInfo;
  await new Promise((r) => server.close(r));
  return port;
}

/**
 * A binary that dies instantly on every platform: `node` rejects the leading
 * `--host`/`--port` ("bad option") and exits in ~25ms.
 */
const diesInstantly = (): string => process.execPath;

async function script(name: string, js: string): Promise<string> {
  const p = path.join(tmp, `${name}.mjs`);
  await fsp.writeFile(p, js);
  return p;
}

/**
 * A spawnable surrogate for the frozen sidecar. POSIX only: Node refuses to
 * spawn a `.cmd` without `shell: true`, and the real artifact is an `.exe`.
 */
async function fakeBinary(name: string, js: string): Promise<string> {
  const target = await script(name, js);
  const sh = path.join(tmp, name);
  await fsp.writeFile(sh, `#!/bin/sh\nexec node "${target}" "$@"\n`, { mode: 0o755 });
  return sh;
}

const posixOnly = process.platform === "win32" ? describe.skip : describe;

describe("startSidecar vs a FOREIGN server already on the port", () => {
  it("refuses the start, naming the port, instead of attaching to it", async () => {
    const port = await foreignServer();
    const p = startSidecar({
      binaryPath: diesInstantly(),
      port,
      autoCleanup: false,
      healthTimeoutMs: 20_000,
    });
    await expect(p).rejects.toBeInstanceOf(PortInUseError);
    await expect(p).rejects.toThrow(new RegExp(`port ${port}`));
    await expect(p).rejects.toThrow(/connectSidecar/);
  }, 30_000);

  it("never spawns a child when the port is taken", async () => {
    // spawnSidecar throws BinaryNotFoundError the moment it is reached, so a
    // PortInUseError proves the port check ran first.
    const port = await foreignServer();
    const p = startSidecar({
      binaryPath: path.join(tmp, "never-created"),
      port,
      autoCleanup: false,
    });
    await expect(p).rejects.toBeInstanceOf(PortInUseError);
    await expect(p).rejects.not.toBeInstanceOf(BinaryNotFoundError);
  }, 30_000);

  it("is an AgentEmailError, so callers catching the package base class see it", async () => {
    const port = await foreignServer();
    await expect(
      startSidecar({ binaryPath: diesInstantly(), port, autoCleanup: false }),
    ).rejects.toBeInstanceOf(AgentEmailError);
  }, 30_000);

  it("keeps the health-timeout error when the port is genuinely free", async () => {
    const port = await freePort();
    const p = startSidecar({
      binaryPath: diesInstantly(),
      port,
      autoCleanup: false,
      healthTimeoutMs: 3_000,
    });
    await expect(p).rejects.toBeInstanceOf(HealthTimeoutError);
  }, 30_000);
});

posixOnly("startSidecar when its own child dies", () => {
  it("gives up as soon as the process dies instead of waiting out the health timeout", async () => {
    const bin = await fakeBinary("dies", "process.exit(3)");
    const port = await freePort();
    const started = Date.now();
    await expect(
      startSidecar({ binaryPath: bin, port, autoCleanup: false, healthTimeoutMs: 30_000 }),
    ).rejects.toBeInstanceOf(HealthTimeoutError);
    expect(Date.now() - started).toBeLessThan(15_000);
  }, 40_000);

  it("names the port conflict when something binds after the pre-flight check", async () => {
    // The fake sidecar hands the port to a DETACHED holder and exits — the same
    // shape as the frozen build's uvicorn grandchild.
    const readyFile = path.join(tmp, "holder.ready");
    const holder = await script(
      "holder",
      `import http from "node:http";
       import fs from "node:fs";
       const port = Number(process.argv[2]);
       const server = http.createServer((_q, s) => {
         s.writeHead(200, { "content-type": "application/json" });
         s.end(JSON.stringify({ status: "ok", service: "not-ours" }));
       });
       server.listen(port, "127.0.0.1", () => fs.writeFileSync(${JSON.stringify(readyFile)}, String(process.pid)));
       setTimeout(() => process.exit(0), 20000).unref();`,
    );
    const bin = await fakeBinary(
      "hands-off-port",
      `import { spawn } from "node:child_process";
       import fs from "node:fs";
       const port = process.argv[process.argv.indexOf("--port") + 1];
       const child = spawn(process.execPath, [${JSON.stringify(holder)}, port], { detached: true, stdio: "ignore" });
       child.unref();
       const wait = () => fs.existsSync(${JSON.stringify(readyFile)}) ? process.exit(0) : setTimeout(wait, 20);
       wait();`,
    );

    const port = await freePort();
    try {
      const p = startSidecar({ binaryPath: bin, port, autoCleanup: false, healthTimeoutMs: 15_000 });
      await expect(p).rejects.toBeInstanceOf(SidecarExitedError);
      await expect(p).rejects.toThrow(/another process is already bound/);
    } finally {
      if (fs.existsSync(readyFile)) {
        try {
          process.kill(Number(fs.readFileSync(readyFile, "utf8")), "SIGKILL");
        } catch {
          /* already gone */
        }
      }
    }
  }, 40_000);
});
