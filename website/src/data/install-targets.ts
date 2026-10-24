// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What the header's Install button downloads per platform: the desktop app,
// the same file as the Agent UI card's button. A platform with no desktop build
// on this release has no entry, so Install keeps its jump to the section there.

import type { StableRelease } from './github';
import { DESKTOP_PLATFORMS, PLATFORM_LABELS, desktopArtifactFileName, type PlatformKey } from '../scripts/download-target';

export interface InstallTarget {
  href: string;
  label: string;
}

/** The header's per-platform targets on this release. */
export function installTargets(
  release: Pick<StableRelease, 'version' | 'assets'>,
): Partial<Record<PlatformKey, InstallTarget>> {
  const byName = new Map(release.assets.map((a) => [a.name, a]));
  return Object.fromEntries(
    DESKTOP_PLATFORMS.flatMap((platform) => {
      const name = desktopArtifactFileName(platform, release.version);
      const asset = name ? byName.get(name) : undefined;
      if (!asset) return [];
      const label = `Download the GAIA desktop app for ${PLATFORM_LABELS[platform]} (${asset.name})`;
      return [[platform, { href: asset.url, label }]];
    }),
  );
}
