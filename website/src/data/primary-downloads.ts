// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The one primary download per platform, shared by the install section's top
// button and the header's Install button so the two can never disagree.
//
// The desktop app wherever it builds, the terminal app where it does not, and
// on Windows x64 (from WINDOWS_SETUP_SINCE) the setup that installs the desktop
// app, the terminal app, or both. Every file is looked up on the release by its
// exact name: a missing primary file drops the platform, never promotes another
// file, and a missing option drops just that option.

import type { ReleaseAsset, StableRelease } from './github';
import {
  DESKTOP_DOWNLOAD_LABELS,
  DOWNLOAD_LABELS,
  INSTALLERS_FOR,
  INSTALLER_CTA_LABELS,
  PLATFORM_LABELS,
  PLATFORM_ORDER,
  WINDOWS_INSTALL_COMMAND,
  artifactFileName,
  desktopArtifactFileName,
  desktopExtraFileNames,
  windowsSetupOffersChoice,
  type PlatformKey,
} from '../scripts/download-target';

export interface Choice {
  asset: ReleaseAsset;
  label: string;
  note?: string;
  command?: string;
}

export interface PrimaryDownload {
  platform: PlatformKey;
  // What the button installs: the Windows setup, the desktop app, or the terminal.
  kind: 'setup' | 'desktop' | 'terminal';
  asset: ReleaseAsset;
  cta: string;
  caption: string;
  lede: string;
  // The platform's other files, folded under the button.
  options: Choice[];
}

const BINARY_NOTE = 'A single file with no installer: put it on your PATH yourself.';

/** Every platform's primary download on this release, in PLATFORM_ORDER. */
export function primaryDownloads(release: Pick<StableRelease, 'version' | 'assets'>): PrimaryDownload[] {
  const { version } = release;
  const byName = new Map(release.assets.map((a) => [a.name, a]));
  const published = (name: string | null) => (name ? byName.get(name) : undefined);

  const terminalChoices = (platform: PlatformKey): Choice[] => {
    const choices: Choice[] = INSTALLERS_FOR[platform].flatMap((key) => {
      const asset = published(artifactFileName(key, version));
      return asset ? [{ asset, label: `Terminal app — ${DOWNLOAD_LABELS[key]}` }] : [];
    });
    const binary = published(artifactFileName(platform, version));
    if (binary) {
      choices.push({ asset: binary, label: `Terminal app — ${DOWNLOAD_LABELS[platform]}`, note: BINARY_NOTE });
    }
    return choices;
  };

  function windowsSetup(platform: PlatformKey): PrimaryDownload | null {
    const setup = published(artifactFileName('win-x64-setup', version));
    if (!setup) return null;
    const options: Choice[] = [];
    const desktopOnly = published(desktopArtifactFileName(platform, version));
    if (desktopOnly) {
      options.push({
        asset: desktopOnly,
        label: 'Desktop app only (.exe)',
        note: 'Installs just the desktop app. It downloads the GAIA agent the first time it runs.',
      });
    }
    const binary = published(artifactFileName(platform, version));
    if (binary) {
      options.push({
        asset: binary,
        label: 'Terminal app only (.exe)',
        note: 'The terminal app as a single file, with no installer: put it on your PATH yourself. Or let PowerShell install it for you:',
        command: WINDOWS_INSTALL_COMMAND,
      });
    }
    return {
      platform,
      kind: 'setup',
      asset: setup,
      cta: INSTALLER_CTA_LABELS['win-x64-setup'],
      caption: 'Installer (.exe)',
      lede: 'One installer: it installs the desktop app, the terminal app, or both, and asks which while it runs. Both share the same local models.',
      options,
    };
  }

  function primaryFor(platform: PlatformKey): PrimaryDownload | null {
    const label = PLATFORM_LABELS[platform];
    if (platform === 'win-x64' && windowsSetupOffersChoice(version)) return windowsSetup(platform);

    const desktopName = desktopArtifactFileName(platform, version);
    if (desktopName) {
      const desktop = published(desktopName);
      if (!desktop) return null;
      const extras = desktopExtraFileNames(platform, version).flatMap(({ label: l, name }) => {
        const asset = published(name);
        return asset ? [{ asset, label: l }] : [];
      });
      const terminal = terminalChoices(platform);
      return {
        platform,
        kind: 'desktop',
        asset: desktop,
        cta: `Download for ${label}`,
        caption: `Desktop app — ${DESKTOP_DOWNLOAD_LABELS[platform]}`,
        lede:
          'The GAIA desktop app: chat, your documents and your files, with every step shown as it runs.' +
          (terminal.length ? ' The terminal app is under more options.' : ''),
        options: [...extras, ...terminal],
      };
    }

    // No desktop build here: the terminal app is the primary download.
    const [first, ...rest] = terminalChoices(platform);
    if (!first) return null;
    return {
      platform,
      kind: 'terminal',
      asset: first.asset,
      cta: `Download for ${label}`,
      caption: first.label,
      lede: `There is no desktop app for ${label}, so this is the GAIA terminal app.${first.note ? ` ${first.note}` : ''}`,
      options: rest,
    };
  }

  return PLATFORM_ORDER.flatMap((p) => primaryFor(p) ?? []);
}

/** What the header's Install button becomes on a machine placed on one platform. */
export interface InstallTarget {
  href: string;
  label: string;
}

/** The header's per-platform targets, from the same primary downloads. */
export function installTargets(rows: PrimaryDownload[]): Partial<Record<PlatformKey, InstallTarget>> {
  return Object.fromEntries(
    rows.map((r) => [
      r.platform,
      { href: r.asset.url, label: `Download GAIA for ${PLATFORM_LABELS[r.platform]} (${r.asset.name})` },
    ]),
  );
}
