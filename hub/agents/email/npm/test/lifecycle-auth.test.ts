// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Caller-auth handshake (#1706): spawnSidecar mints a per-session token, hands it
 * to the sidecar over the private GAIA_EMAIL_SIDECAR_TOKEN env channel (never on
 * the command line), and binds it to the sidecar's client.
 *
 * Uses vi.doMock + vi.resetModules so each test gets fresh module state, and
 * autoCleanup:false so no process signal handlers are installed.
 */

import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeFakeChild(pid = 4242) {
  return Object.assign(new EventEmitter(), {
    pid,
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    stdout: new EventEmitter(),
    stderr: new EventEmitter(),
    kill: vi.fn(),
  });
}

interface SpawnCall {
  args: string[];
  options: { env?: Record<string, string | undefined> };
}

async function loadLifecycleCapturingSpawn(calls: SpawnCall[]) {
  const fakeChild = makeFakeChild();
  vi.doMock("node:child_process", () => ({
    spawn: vi.fn((_bin: string, args: string[], options: SpawnCall["options"]) => {
      calls.push({ args, options });
      return fakeChild;
    }),
    spawnSync: vi.fn(),
  }));
  vi.doMock("node:fs", () => ({
    default: { existsSync: vi.fn(() => true) },
  }));
  return import("../src/lifecycle.js");
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("generateSessionToken", () => {
  it("mints distinct, URL-safe, high-entropy tokens", async () => {
    const { generateSessionToken } = await import("../src/lifecycle.js");
    const a = generateSessionToken();
    const b = generateSessionToken();
    expect(a).not.toBe(b);
    expect(a.length).toBeGreaterThanOrEqual(32);
    expect(a).toMatch(/^[A-Za-z0-9_-]+$/); // base64url alphabet
  });
});

describe("spawnSidecar caller-auth token (#1706)", () => {
  it("passes the token via env, not argv, and binds it to the sidecar", async () => {
    const calls: SpawnCall[] = [];
    const { spawnSidecar } = await loadLifecycleCapturingSpawn(calls);

    const sidecar = spawnSidecar({
      binaryPath: "/fake/email-agent",
      port: 8131,
      autoCleanup: false,
    });

    expect(sidecar.authToken).toBeTruthy();
    expect(calls).toHaveLength(1);
    const { args, options } = calls[0]!;
    // Handed over the private env channel...
    expect(options.env?.GAIA_EMAIL_SIDECAR_TOKEN).toBe(sidecar.authToken);
    // ...and never on the command line (which shows up in a process listing).
    expect(args.join(" ")).not.toContain(sidecar.authToken);
    expect(args.join(" ")).not.toContain("GAIA_EMAIL_SIDECAR_TOKEN");
  });

  it("honors an explicitly supplied token", async () => {
    const calls: SpawnCall[] = [];
    const { spawnSidecar } = await loadLifecycleCapturingSpawn(calls);

    const sidecar = spawnSidecar({
      binaryPath: "/fake/email-agent",
      authToken: "explicit-token",
      autoCleanup: false,
    });

    expect(sidecar.authToken).toBe("explicit-token");
    expect(calls[0]!.options.env?.GAIA_EMAIL_SIDECAR_TOKEN).toBe("explicit-token");
  });

  it("preserves inherited + caller-supplied env alongside the token", async () => {
    const calls: SpawnCall[] = [];
    const { spawnSidecar } = await loadLifecycleCapturingSpawn(calls);

    spawnSidecar({
      binaryPath: "/fake/email-agent",
      env: { GAIA_EMAIL_AGENT_MODE: "user" },
      autoCleanup: false,
    });

    const env = calls[0]!.options.env ?? {};
    expect(env.GAIA_EMAIL_AGENT_MODE).toBe("user");
    expect(env.GAIA_EMAIL_SIDECAR_TOKEN).toBeTruthy();
  });
});
