// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `gaia serve` must not report a clean exit when Ctrl+C fails to stop the
 * sidecar: the port stays bound, so the next `serve` would fail unexplained.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SHUTDOWN_ERROR =
  "the gaia sidecar (pid 4242) did not exit after a forced kill. Kill it manually — kill -9 -4242 — or port 8141 stays bound.";

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

/** Wait for `serve` to install its SIGINT handler, then call it as a Ctrl+C would. */
async function pressCtrlC(before: Function[]): Promise<void> {
  for (let i = 0; i < 200; i++) {
    const added = process.listeners("SIGINT").filter((l) => !before.includes(l));
    if (added.length > 0) {
      for (const l of added) (l as () => void)();
      return;
    }
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("serve never installed a SIGINT handler");
}

async function runServe(shutdownImpl: () => Promise<void>): Promise<number> {
  vi.doMock("../src/fetch.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../src/fetch.js")>()),
    fetchBinary: vi.fn(async () => ({ binaryPath: "/fake/gaia-agent", cached: true })),
  }));
  vi.doMock("../src/lifecycle.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../src/lifecycle.js")>()),
    startSidecar: vi.fn(async () => ({
      child: { pid: 4242 },
      host: "127.0.0.1",
      port: 8141,
      baseUrl: "http://127.0.0.1:8141",
    })),
    shutdown: vi.fn(shutdownImpl),
  }));
  const { main } = await import("../src/cli.js");
  const before = process.listeners("SIGINT").slice();
  const running = main(["serve"]);
  await pressCtrlC(before);
  return running;
}

describe("gaia serve on Ctrl+C", () => {
  it("exits 1 and prints the shutdown error when the sidecar survives", async () => {
    const code = await runServe(async () => {
      throw new Error(SHUTDOWN_ERROR);
    });
    expect(code).toBe(1);
    expect(stderr.join("")).toContain("pid 4242");
    expect(stderr.join("")).toContain("kill -9 -4242");
  });

  it("exits 0 when the sidecar stops cleanly", async () => {
    expect(await runServe(async () => undefined)).toBe(0);
  });
});
