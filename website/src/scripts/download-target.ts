// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Which Agent UI installer the download CTA is allowed to deep-link to.
//
// macOS is arm64-only (src/gaia/apps/webui/electron-builder.yml builds one dmg,
// arch: [arm64]) and Rosetta translates x64 -> arm, not the reverse, so an Intel
// Mac cannot run it. Chromium also masks the architecture in the UA string on
// Apple Silicon, so a Mac resolves to a target ONLY when the client hints
// confirm `arm`; anything else resolves to null and the caller keeps its
// releases-page href, which lets the visitor pick for themselves.

export type DownloadTarget = 'windows' | 'macos-arm64' | 'linux';

export interface UserAgentDataLike {
  getHighEntropyValues?(hints: string[]): Promise<{ architecture?: string }>;
}

export interface NavigatorLike {
  userAgent: string;
  userAgentData?: UserAgentDataLike;
}

export const TARGET_LABELS: Record<DownloadTarget, string> = {
  windows: 'Windows',
  'macos-arm64': 'macOS (Apple Silicon)',
  linux: 'Linux',
};

// Anchored so the `.blockmap` siblings in the release assets never match.
export const TARGET_ASSETS: Record<DownloadTarget, RegExp> = {
  windows: /x64-setup\.exe$/,
  'macos-arm64': /arm64\.dmg$/,
  linux: /\.AppImage$/,
};

export function detectOS(userAgent: string): 'windows' | 'macos' | 'linux' | null {
  if (/Windows/i.test(userAgent)) return 'windows';
  if (/Mac/i.test(userAgent) && !/iPhone|iPad|iPod/i.test(userAgent)) return 'macos';
  if (/Linux/i.test(userAgent) && !/Android/i.test(userAgent)) return 'linux';
  return null;
}

export async function resolveDownloadTarget(
  nav: NavigatorLike,
): Promise<DownloadTarget | null> {
  const os = detectOS(nav.userAgent);
  if (os === 'windows' || os === 'linux') return os;
  if (os !== 'macos') return null;

  const uaData = nav.userAgentData;
  if (!uaData?.getHighEntropyValues) return null;
  try {
    const hints = await uaData.getHighEntropyValues(['architecture']);
    return hints?.architecture === 'arm' ? 'macos-arm64' : null;
  } catch {
    // Hints can be refused; an unknown architecture is not Apple Silicon.
    return null;
  }
}
