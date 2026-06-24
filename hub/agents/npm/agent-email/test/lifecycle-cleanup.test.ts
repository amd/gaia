// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Unit tests for the auto-cleanup feature in spawnSidecar.
 *
 * Uses vi.doMock (not hoisted) + vi.resetModules() so each test gets
 * fresh module state (cleanupInstalled = false, liveSidecars empty).
 */

import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Helper: minimal fake ChildProcess
// ---------------------------------------------------------------------------
function makeFakeChild(pid = 12345) {
  const child = Object.assign(new EventEmitter(), {
    pid,
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    stdout: new EventEmitter(),
    stderr: new EventEmitter(),
    kill: vi.fn(),
  });
  return child;
}

// ---------------------------------------------------------------------------
// Shared setup: capture+restore process listeners around every test so
// the auto-cleanup handlers one test installs don't pollute the next.
// ---------------------------------------------------------------------------
const WATCHED_EVENTS = ["exit", "SIGINT", "SIGTERM", "SIGHUP", "uncaughtException", "unhandledRejection"] as const;

type SavedListeners = Map<string, ((...args: unknown[]) => void)[]>;

function captureListeners(): SavedListeners {
  const map: SavedListeners = new Map();
  for (const ev of WATCHED_EVENTS) {
    map.set(ev, process.listeners(ev as NodeJS.Signals).slice() as ((...args: unknown[]) => void)[]);
  }
  return map;
}

function restoreListeners(saved: SavedListeners): void {
  for (const ev of WATCHED_EVENTS) {
    const current = process.listeners(ev as NodeJS.Signals).slice();
    for (const fn of current) process.removeListener(ev as NodeJS.Signals, fn as (...args: unknown[]) => void);
    for (const fn of (saved.get(ev) ?? [])) process.on(ev as NodeJS.Signals, fn as (...args: unknown[]) => void);
  }
}

let savedListeners: SavedListeners;

beforeEach(() => {
  savedListeners = captureListeners();
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
  restoreListeners(savedListeners);
});

// ---------------------------------------------------------------------------
// Helper: load a fresh lifecycle module with mocked child_process + fs
// ---------------------------------------------------------------------------
async function loadLifecycle(fakeChild: ReturnType<typeof makeFakeChild>) {
  vi.doMock("node:child_process", () => ({
    spawn: vi.fn(() => fakeChild),
    spawnSync: vi.fn(),
  }));
  vi.doMock("node:fs", () => ({
    default: { existsSync: vi.fn(() => true) },
  }));
  // Dynamic import after doMock — picks up the fresh mocked modules.
  return import("../src/lifecycle.js");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("autoCleanup: true (default) — installs process handlers", () => {
  it("adds an 'exit' listener and a 'SIGINT' listener on first spawn", async () => {
    const fakeChild = makeFakeChild(11111);
    const { spawnSidecar } = await loadLifecycle(fakeChild);

    const exitBefore = process.listenerCount("exit");
    const sigintBefore = process.listenerCount("SIGINT");

    spawnSidecar({ binaryPath: "/fake/email-agent", autoCleanup: true });

    expect(process.listenerCount("exit")).toBeGreaterThan(exitBefore);
    expect(process.listenerCount("SIGINT")).toBeGreaterThan(sigintBefore);
  });

  it("does not add more listeners on a second spawn (idempotent install)", async () => {
    const fakeChild1 = makeFakeChild(22222);
    const fakeChild2 = makeFakeChild(22223);
    let callIdx = 0;

    vi.doMock("node:child_process", () => ({
      spawn: vi.fn(() => (callIdx++ === 0 ? fakeChild1 : fakeChild2)),
      spawnSync: vi.fn(),
    }));
    vi.doMock("node:fs", () => ({
      default: { existsSync: vi.fn(() => true) },
    }));
    const { spawnSidecar } = await import("../src/lifecycle.js");

    spawnSidecar({ binaryPath: "/fake/email-agent" });
    const exitAfterFirst = process.listenerCount("exit");

    spawnSidecar({ binaryPath: "/fake/email-agent" });

    // Second spawn must NOT add another exit listener.
    expect(process.listenerCount("exit")).toBe(exitAfterFirst);
  });
});

describe("reap behavior — the installed SIGINT handler kills the sidecar tree", () => {
  it("SIGKILLs the detached process group (-pid) when the signal handler fires", async () => {
    // Spy on process.kill so BOTH the group-kill (killTreeSync) and the
    // self-re-raise are intercepted and the test process is never signaled.
    const killSpy = vi
      .spyOn(process, "kill")
      .mockImplementation((() => true) as typeof process.kill);
    try {
      const fakeChild = makeFakeChild(12345);
      const { spawnSidecar } = await loadLifecycle(fakeChild);

      spawnSidecar({ binaryPath: "/fake/email-agent" }); // autoCleanup default on

      // The lifecycle handler is the last SIGINT listener registered.
      const sigintListeners = process.listeners("SIGINT");
      const handler = sigintListeners[sigintListeners.length - 1] as () => void;
      expect(typeof handler).toBe("function");

      handler();

      // killTreeSync forks per platform: taskkill on Windows, a negative-pid
      // process-group SIGKILL on POSIX. Assert the branch for the test host.
      if (process.platform === "win32") {
        const { spawnSync } = vi.mocked(await import("node:child_process"));
        expect(spawnSync).toHaveBeenCalledWith(
          "taskkill", ["/PID", "12345", "/T", "/F"], { stdio: "ignore" },
        );
      } else {
        expect(killSpy).toHaveBeenCalledWith(-12345, "SIGKILL");
      }
    } finally {
      killSpy.mockRestore();
    }
  });
});

describe("autoCleanup: false — opts out", () => {
  it("returns a valid Sidecar without installing any process handlers", async () => {
    const fakeChild = makeFakeChild(99999);
    const { spawnSidecar } = await loadLifecycle(fakeChild);

    const exitBefore = process.listenerCount("exit");
    const sigintBefore = process.listenerCount("SIGINT");

    const sidecar = spawnSidecar({ binaryPath: "/fake/email-agent", autoCleanup: false });

    expect(sidecar).toHaveProperty("child");
    expect(sidecar).toHaveProperty("baseUrl");
    expect(sidecar).toHaveProperty("client");

    // Opted out — no new process listeners should have been installed.
    expect(process.listenerCount("exit")).toBe(exitBefore);
    expect(process.listenerCount("SIGINT")).toBe(sigintBefore);
  });
});
