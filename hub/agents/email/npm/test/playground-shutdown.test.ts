// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `agent-email playground` must not report a clean exit when Ctrl+C fails to
 * stop the sidecar: the port stays bound, so the next start would fail
 * unexplained.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SHUTDOWN_ERROR = "taskkill failed for pid 4242";

let stderr: string[];

beforeEach(() => {
  vi.resetModules();
  stderr = [];
  vi.spyOn(process.stderr, "write").mockImplementation((chunk: string | Uint8Array) => {
    stderr.push(String(chunk));
    return true;
  });
  vi.spyOn(process.stdout, "write").mockImplementation(() => true);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.doUnmock("../src/fetch.js");
  vi.doUnmock("../src/lifecycle.js");
});

/** Wait for `playground` to install its SIGINT handler, then call it as a Ctrl+C would. */
async function pressCtrlC(before: Function[]): Promise<void> {
  for (let i = 0; i < 200; i++) {
    const added = process.listeners("SIGINT").filter((l) => !before.includes(l));
    if (added.length > 0) {
      for (const l of added) (l as () => void)();
      return;
    }
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("playground never installed a SIGINT handler");
}

async function runPlayground(shutdownImpl: () => Promise<void>): Promise<number> {
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
});
