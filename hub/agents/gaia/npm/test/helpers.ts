// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Shared fixtures: a synthetic two-lane lock with the real shape. */

import fsp from "node:fs/promises";
import path from "node:path";

import type { BinaryLock, BinaryLockEntry } from "../src/platform.js";
import { TUI_ARTIFACT_NAMES } from "../src/platform.js";

export const FAKE_SHA = "b".repeat(64);

/** The synthetic lanes, deliberately at DIFFERENT versions and base URLs. */
export const SIDECAR_VERSION = "0.1.0";
export const TUI_VERSION = "0.23.0";
export const SIDECAR_BASE = `https://example.test/agents/gaia/${SIDECAR_VERSION}`;
export const TUI_BASE = `https://example.test/agents/terminal-hub/${TUI_VERSION}`;

const SIDECAR_PLATFORMS = ["win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"];
const TUI_PLATFORMS = [...SIDECAR_PLATFORMS, "linux-arm64", "win32-arm64"];

function sidecarEntries(sha256: string): Record<string, BinaryLockEntry> {
  const out: Record<string, BinaryLockEntry> = {};
  for (const p of SIDECAR_PLATFORMS) {
    const win = p.startsWith("win32");
    out[p] = {
      filename: `gaia-agent-${p}${win ? ".exe" : ""}`,
      executable: `gaia-agent${win ? ".exe" : ""}`,
      sha256,
      size: 0,
    };
  }
  return out;
}

/**
 * TUI entries carry the TERMINAL-HUB artifact names (`gaia-win-x64.exe`), keyed
 * by the npm platform key (`win32-x64`) — the win32↔win mapping lives in data.
 */
function tuiEntries(sha256: string): Record<string, BinaryLockEntry> {
  const out: Record<string, BinaryLockEntry> = {};
  for (const p of TUI_PLATFORMS) {
    const win = p.startsWith("win32");
    out[p] = {
      filename: TUI_ARTIFACT_NAMES[p]!,
      executable: `gaia-tui${win ? ".exe" : ""}`,
      sha256,
      size: 0,
    };
  }
  return out;
}

/** A lock with the real platform coverage. `sha256` applies to every entry. */
export function makeLock(sha256: string = FAKE_SHA): BinaryLock {
  return {
    schemaVersion: "3.0",
    agentVersion: SIDECAR_VERSION,
    components: {
      sidecar: {
        componentVersion: SIDECAR_VERSION,
        baseUrl: SIDECAR_BASE,
        platforms: sidecarEntries(sha256),
      },
      tui: {
        componentVersion: TUI_VERSION,
        baseUrl: TUI_BASE,
        platforms: tuiEntries(sha256),
      },
    },
  };
}

export async function writeLockFile(dir: string, lock: BinaryLock): Promise<string> {
  const p = path.join(dir, "binaries.lock.json");
  await fsp.writeFile(p, JSON.stringify(lock, null, 2));
  return p;
}
