// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Shared fixtures: a synthetic dual-component lock with the real shape. */

import fsp from "node:fs/promises";
import path from "node:path";

import type { BinaryLock } from "../src/platform.js";

export const FAKE_SHA = "b".repeat(64);

const SIDECAR_PLATFORMS = ["win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"];
const TUI_PLATFORMS = [...SIDECAR_PLATFORMS, "linux-arm64", "win32-arm64"];

function entriesFor(
  platforms: string[],
  prefix: string,
  exe: string,
  sha256: string,
): Record<string, BinaryLock["components"][string][string]> {
  const out: Record<string, BinaryLock["components"][string][string]> = {};
  for (const p of platforms) {
    const win = p.startsWith("win32");
    out[p] = {
      filename: `${prefix}-${p}${win ? ".exe" : ""}`,
      executable: `${exe}${win ? ".exe" : ""}`,
      sha256,
      size: 0,
    };
  }
  return out;
}

/** A lock with the real platform coverage. `sha256` applies to every entry. */
export function makeLock(sha256: string = FAKE_SHA): BinaryLock {
  return {
    schemaVersion: "2.0",
    agentVersion: "0.1.0",
    baseUrl: "https://example.test/agents/gaia/0.1.0",
    components: {
      sidecar: entriesFor(SIDECAR_PLATFORMS, "gaia-agent", "gaia-agent", sha256),
      tui: entriesFor(TUI_PLATFORMS, "gaia-tui", "gaia-tui", sha256),
    },
  };
}

export async function writeLockFile(dir: string, lock: BinaryLock): Promise<string> {
  const p = path.join(dir, "binaries.lock.json");
  await fsp.writeFile(p, JSON.stringify(lock, null, 2));
  return p;
}
