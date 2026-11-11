// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What the header's Install button downloads per platform. Windows x64 gets the
// combined setup, which installs the desktop app, the terminal, or both; macOS
// and Linux have no combined installer, so they get the desktop app. A
// platform without its file on this release has no entry, so Install keeps its
// jump to the section there.

import type { ReleaseAsset, StableRelease } from './github';
import {
  DESKTOP_PLATFORMS,
  PLATFORM_LABELS,
  artifactFileName,
  desktopArtifactFileName,
  windowsSetupOffersChoice,
  type PlatformKey,
} from '../scripts/download-target';

type Release = Pick<StableRelease, 'tag' | 'version' | 'assets'>;

export interface InstallTarget {
  href: string;
  label: string;
}

/**
 * The Windows setup that asks desktop app / terminal / both, or undefined when
 * the release does not carry it. Earlier releases used the same filename for a
 * terminal-only setup, hence the version gate.
 */
export function windowsCombinedSetup(release: Release): ReleaseAsset | undefined {
  if (!windowsSetupOffersChoice(release.version)) return undefined;
  const name = artifactFileName('win-x64-setup', release.version);
  return release.assets.find((a) => a.name === name);
}

/** The header's per-platform targets on this release. */
export function installTargets(release: Release): Partial<Record<PlatformKey, InstallTarget>> {
  const byName = new Map(release.assets.map((a) => [a.name, a]));
  const targets: Partial<Record<PlatformKey, InstallTarget>> = {};

  const setup = windowsCombinedSetup(release);
  if (setup) {
    targets['win-x64'] = {
      href: setup.url,
      label: `Download the GAIA installer for ${PLATFORM_LABELS['win-x64']}: desktop app, terminal, or both (${setup.name})`,
    };
  }
  for (const platform of DESKTOP_PLATFORMS.filter((p) => p !== 'win-x64')) {
    const name = desktopArtifactFileName(platform, release.version);
    const asset = name ? byName.get(name) : undefined;
    if (!asset) continue;
    targets[platform] = {
      href: asset.url,
      label: `Download the GAIA desktop app for ${PLATFORM_LABELS[platform]} (${asset.name})`,
    };
  }

  // No target at all means the asset names drifted, not a coverage gap:
  // Install would silently become a section link on every machine.
  if (!Object.keys(targets).length) {
    throw new Error(
      `[install] ${release.tag} carries ${release.assets.length} assets, none of which the header's ` +
        `Install button can download (${artifactFileName('win-x64-setup', release.version)}, ` +
        `${DESKTOP_PLATFORMS.filter((p) => p !== 'win-x64')
          .map((p) => desktopArtifactFileName(p, release.version))
          .join(', ')}). Check https://github.com/amd/gaia/releases/tag/${release.tag}; if the names ` +
        `changed, update src/scripts/download-target.ts.`,
    );
  }
  return targets;
}
