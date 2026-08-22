// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Which published terminal-hub binary this visitor's machine can run.
//
// The hub publishes six: {win,darwin,linux} x {x64,arm64}, so unlike a
// single-architecture installer this has to resolve the ARCHITECTURE too, and
// only the client hints report it truthfully — every OS masks arm64 in the UA
// string to keep old sites working. A machine we cannot place resolves to null
// and the caller keeps its full platform list on screen, which is the honest
// outcome: an arm64 user handed an x64 binary gets a failure at exec time, not
// a download error they can act on.

export type Os = 'win' | 'darwin' | 'linux';
export type Arch = 'x64' | 'arm64';
/** Matches the hub's platform keys, e.g. `win-x64`. */
export type PlatformKey = `${Os}-${Arch}`;

export interface UserAgentDataLike {
  getHighEntropyValues?(hints: string[]): Promise<{ architecture?: string }>;
}

export interface NavigatorLike {
  userAgent: string;
  platform?: string;
  maxTouchPoints?: number;
  userAgentData?: UserAgentDataLike;
}

export const OS_LABELS: Record<Os, string> = {
  win: 'Windows',
  darwin: 'macOS',
  linux: 'Linux',
};

export const PLATFORM_LABELS: Record<PlatformKey, string> = {
  'win-x64': 'Windows (x64)',
  'win-arm64': 'Windows (ARM64)',
  'darwin-x64': 'macOS (Intel)',
  'darwin-arm64': 'macOS (Apple Silicon)',
  'linux-x64': 'Linux (x64)',
  'linux-arm64': 'Linux (ARM64)',
};

export function detectOs(userAgent: string): Os | null {
  if (/Windows/i.test(userAgent)) return 'win';
  if (/Mac/i.test(userAgent) && !/iPhone|iPad|iPod/i.test(userAgent)) return 'darwin';
  if (/Linux/i.test(userAgent) && !/Android/i.test(userAgent)) return 'linux';
  return null;
}

/**
 * Apple Silicon without client hints (Safari): the UA says "Intel" on every
 * Mac, so the UA cannot decide it. A Mac reporting touch points is an M-series
 * machine — Intel Macs report 0 — which is the one signal Safari does expose.
 */
function appleSiliconBySideChannel(nav: NavigatorLike): boolean {
  return (nav.maxTouchPoints ?? 0) > 0;
}

export async function resolveArch(nav: NavigatorLike, os: Os): Promise<Arch | null> {
  const hints = nav.userAgentData?.getHighEntropyValues;
  if (hints) {
    try {
      const { architecture } = await nav.userAgentData!.getHighEntropyValues!([
        'architecture',
      ]);
      if (architecture === 'arm') return 'arm64';
      if (architecture === 'x86') return 'x64';
    } catch {
      // Hints can be refused outright; fall through to the UA evidence.
    }
  }

  // Explicit arm64 in the UA — Linux and Windows do sometimes say so.
  if (/aarch64|arm64/i.test(nav.userAgent)) return 'arm64';
  if (os === 'darwin') return appleSiliconBySideChannel(nav) ? 'arm64' : null;
  // Windows and Linux on arm64 without hints are rare enough that x64 is the
  // safe read; on macOS it is not, which is why that case returns null above.
  if (/x86_64|win64|wow64|x64|amd64|intel/i.test(nav.userAgent)) return 'x64';
  return null;
}

export async function resolvePlatform(nav: NavigatorLike): Promise<PlatformKey | null> {
  const os = detectOs(nav.userAgent);
  if (!os) return null;
  const arch = await resolveArch(nav, os);
  if (!arch) return null;
  return `${os}-${arch}`;
}

/** The hub's artifact filename for a platform, e.g. `win-x64` -> `gaia-win-x64.exe`. */
export function artifactName(platform: PlatformKey): string {
  return platform.startsWith('win-') ? `gaia-${platform}.exe` : `gaia-${platform}`;
}
