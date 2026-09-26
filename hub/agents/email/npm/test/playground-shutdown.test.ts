// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `agent-email playground` must not report a clean exit when Ctrl+C fails to
 * stop the sidecar: the port stays bound, so the next start would fail
 * unexplained.
 */

import { EventEmitter } from "node:events";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SHUTDOWN_ERROR = "taskkill failed for pid 4242";

let stderr: string[];
/** Handlers `playground` installed, so a test can drop them again. */
let installed: Array<[NodeJS.Signals, (...a: unknown[]) => void]>;

beforeEach(() => {
  vi.resetModules();
  stderr = [];
  installed = [];
  vi.spyOn(process.stderr, "write").mockImplementation((chunk: string | Uint8Array) => {
    stderr.push(String(chunk));
    return true;
  });
  vi.spyOn(process.stdout, "write").mockImplementation(() => true);
});

afterEach(() => {
  // `pressCtrlC` calls the listeners directly, and a directly-called `once`
  // listener is never removed — left behind they suppress Node's default
  // signal disposition for the whole vitest worker.
  for (const [sig, listener] of installed) process.removeListener(sig, listener);
  installed = [];
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.doUnmock("../src/fetch.js");
  vi.doUnmock("../src/lifecycle.js");
});

/** Wait for `playground` to install its SIGINT handler, then call it as a Ctrl+C would. */
async function waitForHandlers(before: Function[]): Promise<Array<() => void>> {
  for (let i = 0; i < 200; i++) {
    const added = process.listeners("SIGINT").filter((l) => !before.includes(l));
    if (added.length > 0) {
      for (const l of added) {
        installed.push(["SIGINT", l as (...a: unknown[]) => void]);
      }
      return added as Array<() => void>;
    }
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("playground never installed a SIGINT handler");
}

/** Wait for `playground` to install its SIGINT handler, then call it as a Ctrl+C would. */
async function pressCtrlC(before: Function[]): Promise<Array<() => void>> {
  const handlers = await waitForHandlers(before);
  for (const h of handlers) h();
  return handlers;
}

async function startPlayground(shutdownImpl: () => Promise<void>) {
  vi.doMock("../src/fetch.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../src/fetch.js")>()),
    fetchBinary: vi.fn(async () => ({ binaryPath: "/fake/email-agent", cached: true })),
  }));
  vi.doMock("../src/lifecycle.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../src/lifecycle.js")>()),
    startSidecar: vi.fn(async () => ({
      child: { pid: 4242 },
      host: "127.0.0.1",
      port: 8131,
      baseUrl: "http://127.0.0.1:8131",
    })),
    shutdown: vi.fn(shutdownImpl),
  }));
  const { main } = await import("../src/cli.js");
  const before = process.listeners("SIGINT").slice();
  const running = main(["playground", "--no-open"]);
  return { running, before };
}

async function runPlayground(shutdownImpl: () => Promise<void>): Promise<number> {
  const { running, before } = await startPlayground(shutdownImpl);
  await pressCtrlC(before);
  return running;
}

describe("agent-email playground on Ctrl+C", () => {
  it("exits 1 and prints the shutdown error instead of discarding it", async () => {
    const code = await runPlayground(async () => {
      throw new Error(SHUTDOWN_ERROR);
    });
    expect(code).toBe(1);
    expect(stderr.join("")).toContain(SHUTDOWN_ERROR);
  });

  it("exits 0 when the sidecar stops cleanly", async () => {
    expect(await runPlayground(async () => undefined)).toBe(0);
  });

  it("keeps its handler installed for a second Ctrl+C during teardown", async () => {
    // Emitted, not called directly: `emit` is what removes a `once` listener,
    // and a removed one means the NEXT Ctrl+C hits Node's default disposition,
    // killing the process mid-teardown and orphaning the sidecar on the port.
    let release!: () => void;
    const slow = new Promise<void>((r) => (release = r));
    const { running, before } = await startPlayground(() => slow);
    await waitForHandlers(before);

    process.emit("SIGINT", "SIGINT");

    // Never emit a second SIGINT here — if this assertion is going to fail,
    // there is no listener left and the emit would terminate the worker.
    expect(
      process.listeners("SIGINT").filter((l) => !before.includes(l)).length,
    ).toBeGreaterThan(0);

    // The absorbed repeat says so rather than looking like a frozen terminal.
    for (const l of process.listeners("SIGINT").filter((l) => !before.includes(l))) {
      (l as () => void)();
    }
    expect(stderr.join("")).toContain("already stopping the sidecar (pid 4242)");

    release();
    expect(await running).toBe(0);
    // Removed on the way out, or Ctrl+C stops working for everything after.
    expect(process.listeners("SIGINT").filter((l) => !before.includes(l))).toHaveLength(
      0,
    );
  });

  it.skipIf(process.platform === "win32")(
    "exits 1 naming the pid when the real shutdown can't stop the sidecar",
    async () => {
      vi.spyOn(process, "kill").mockImplementation((() => true) as typeof process.kill);
      const child = Object.assign(new EventEmitter(), {
        pid: 4243, // never emits "exit", even after SIGKILL
        exitCode: null,
        signalCode: null,
        kill: vi.fn(),
      });
      vi.doMock("../src/fetch.js", async (importOriginal) => ({
        ...(await importOriginal<typeof import("../src/fetch.js")>()),
        fetchBinary: vi.fn(async () => ({ binaryPath: "/fake/email-agent", cached: true })),
      }));
      // `shutdown` is deliberately left unmocked — this exercises the real one.
      vi.doMock("../src/lifecycle.js", async (importOriginal) => ({
        ...(await importOriginal<typeof import("../src/lifecycle.js")>()),
        startSidecar: vi.fn(async () => ({
          child,
          host: "127.0.0.1",
          port: 8131,
          baseUrl: "http://127.0.0.1:8131",
        })),
      }));
      const { main } = await import("../src/cli.js");
      const before = process.listeners("SIGINT").slice();
      const running = main(["playground", "--no-open"]);
      const handlers = await waitForHandlers(before);

      vi.useFakeTimers();
      for (const l of handlers) l();
      await vi.advanceTimersByTimeAsync(10_000); // SIGTERM wait + post-SIGKILL wait

      expect(await running).toBe(1);
      expect(stderr.join("")).toContain("pid 4243");
      expect(stderr.join("")).toContain("kill -9 -4243");
    },
  );
});
