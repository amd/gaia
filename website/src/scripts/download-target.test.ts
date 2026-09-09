// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';

import {
  DOWNLOAD_LABELS,
  INSTALLERS_FOR,
  INSTALLER_CTA_LABELS,
  PLATFORM_LABELS,
  artifactFileName,
  detectOs,
  placeOs,
  resolveArch,
  resolveTargets,
  type DownloadKey,
  type NavigatorLike,
  type PlatformKey,
} from './download-target';

// The version and the six filenames the hub is serving today, copied from
// https://hub.amd-gaia.ai/agents/terminal-hub/manifest.json — that release
// publishes NO installers, which is the "installer absent" case below.
const V = '0.23.0';
const RAW_BINARIES = [
  'gaia-linux-x64',
  'gaia-linux-arm64',
  'gaia-darwin-x64',
  'gaia-darwin-arm64',
  'gaia-win-x64.exe',
  'gaia-win-arm64.exe',
];
// What release_components.yml publishes alongside them from this branch on.
const INSTALLER_FILES = [
  'gaia-0.23.0-win-x64-setup.exe',
  'gaia-0.23.0-darwin-arm64.pkg',
  'gaia-0.23.0-darwin-x64.pkg',
  'gaia_0.23.0_amd64.deb',
  'gaia-0.23.0.x86_64.rpm',
];
// The same order TuiDownload.astro renders.
const ORDER: PlatformKey[] = [
  'win-x64',
  'darwin-arm64',
  'linux-x64',
  'win-arm64',
  'darwin-x64',
  'linux-arm64',
];

const UA = {
  win: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  macSafari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
  linux:
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  linuxArm:
    'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  iphone:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1',
  android:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36',
};

const nav = (
  userAgent: string,
  opts: { architecture?: string; hintsThrow?: boolean; maxTouchPoints?: number } = {}
): NavigatorLike => ({
  userAgent,
  maxTouchPoints: opts.maxTouchPoints ?? 0,
  userAgentData:
    opts.architecture !== undefined || opts.hintsThrow
      ? {
          getHighEntropyValues: async () => {
            if (opts.hintsThrow) throw new Error('refused');
            return { architecture: opts.architecture };
          },
        }
      : undefined,
});

describe('detectOs', () => {
  it('places the three desktop platforms', () => {
    expect(detectOs(UA.win)).toBe('win');
    expect(detectOs(UA.mac)).toBe('darwin');
    expect(detectOs(UA.linux)).toBe('linux');
  });

  // Mobile has no build, and iOS/Android UAs both carry a desktop OS token —
  // matching them would hand a phone an x64 desktop binary.
  it('refuses mobile', () => {
    expect(detectOs(UA.iphone)).toBeNull();
    expect(detectOs(UA.android)).toBeNull();
  });
});

describe('resolveArch', () => {
  it('trusts client hints over the UA string', async () => {
    // The UA says "Intel"/"x64" on both; only the hint is truthful.
    expect(await resolveArch(nav(UA.mac, { architecture: 'arm' }), 'darwin')).toBe('arm64');
    expect(await resolveArch(nav(UA.win, { architecture: 'arm' }), 'win')).toBe('arm64');
    expect(await resolveArch(nav(UA.win, { architecture: 'x86' }), 'win')).toBe('x64');
  });

  it('reads an explicit arm64 token when hints are absent', async () => {
    expect(await resolveArch(nav(UA.linuxArm), 'linux')).toBe('arm64');
  });

  it('falls back to x64 on Windows and Linux', async () => {
    expect(await resolveArch(nav(UA.win), 'win')).toBe('x64');
    expect(await resolveArch(nav(UA.linux), 'linux')).toBe('x64');
  });

  it('refuses to guess a Mac architecture with no evidence', async () => {
    expect(await resolveArch(nav(UA.macSafari), 'darwin')).toBeNull();
    expect(await resolveArch(nav(UA.mac, { hintsThrow: true }), 'darwin')).toBeNull();
  });

  // Touch points say nothing about the chip: every Mac reports 0, Intel and
  // Apple Silicon alike. Reading them as an Apple Silicon signal placed every
  // Safari Mac nowhere and handed every iPad a Mac build.
  it('does not read touch points as an architecture signal', async () => {
    expect(await resolveArch(nav(UA.macSafari, { maxTouchPoints: 5 }), 'darwin')).toBeNull();
  });
});

describe('placeOs', () => {
  it('places the desktops we publish for', () => {
    expect(placeOs(nav(UA.win))).toBe('win');
    expect(placeOs(nav(UA.macSafari))).toBe('darwin');
    expect(placeOs(nav(UA.linux))).toBe('linux');
  });

  // iPadOS Safari sends the Mac UA verbatim; touch points are the only tell.
  it('rejects an iPad sending the Mac desktop UA', () => {
    expect(placeOs(nav(UA.macSafari, { maxTouchPoints: 5 }))).toBeNull();
  });

  it('keeps a real Mac, which reports no touch points', () => {
    expect(placeOs(nav(UA.macSafari, { maxTouchPoints: 0 }))).toBe('darwin');
  });
});

describe('resolveTargets', () => {
  it('offers exactly one platform when the machine is placeable', async () => {
    expect(await resolveTargets(nav(UA.win))).toEqual(['win-x64']);
    expect(await resolveTargets(nav(UA.linuxArm))).toEqual(['linux-arm64']);
    expect(await resolveTargets(nav(UA.mac, { architecture: 'arm' }))).toEqual(['darwin-arm64']);
  });

  // The case that regressed: a Mac on Safari must still get an install button.
  it('offers both Mac builds when the architecture cannot be read', async () => {
    expect(await resolveTargets(nav(UA.macSafari))).toEqual(['darwin-arm64', 'darwin-x64']);
  });

  it('offers nothing to a machine we do not publish for', async () => {
    expect(await resolveTargets(nav(UA.iphone))).toEqual([]);
    expect(await resolveTargets(nav(UA.android))).toEqual([]);
    expect(await resolveTargets(nav(UA.macSafari, { maxTouchPoints: 5 }))).toEqual([]);
  });
});

describe('artifactFileName', () => {
  // These are the exact filenames release_components.yml publishes; a wrong one
  // is not a build failure anywhere, it is a 404 on the visitor's click.
  it('matches the hub binary filenames, .exe on Windows only', () => {
    expect(artifactFileName('win-x64', V)).toBe('gaia-win-x64.exe');
    expect(artifactFileName('win-arm64', V)).toBe('gaia-win-arm64.exe');
    expect(artifactFileName('darwin-arm64', V)).toBe('gaia-darwin-arm64');
    expect(artifactFileName('linux-x64', V)).toBe('gaia-linux-x64');
  });

  // The raw binaries are published version-less; only the installers carry it.
  it('ignores the version for the raw binaries', () => {
    expect(artifactFileName('linux-x64', '9.9.9')).toBe('gaia-linux-x64');
  });

  it('matches the installer filenames, each in its own packaging convention', () => {
    expect(artifactFileName('win-x64-setup', V)).toBe('gaia-0.23.0-win-x64-setup.exe');
    expect(artifactFileName('darwin-arm64-pkg', V)).toBe('gaia-0.23.0-darwin-arm64.pkg');
    expect(artifactFileName('darwin-x64-pkg', V)).toBe('gaia-0.23.0-darwin-x64.pkg');
    // dpkg and rpm each parse the version out of the name, so these are not
    // free to follow the gaia-<version>-<platform> house style.
    expect(artifactFileName('linux-x64-deb', V)).toBe('gaia_0.23.0_amd64.deb');
    expect(artifactFileName('linux-x64-rpm', V)).toBe('gaia-0.23.0.x86_64.rpm');
  });

  // The regression this replaces: TuiDownload derived the platform by stripping
  // `gaia-`/`.exe` off the filename, which turned the setup into the key
  // "0.23.0-win-x64-setup" and then filtered every installer off the page.
  it('round-trips a version-carrying installer name back to its key', () => {
    const keys: DownloadKey[] = [
      ...ORDER,
      'win-x64-setup',
      'darwin-arm64-pkg',
      'darwin-x64-pkg',
      'linux-x64-deb',
      'linux-x64-rpm',
    ];
    const byFilename = new Map(keys.map((k) => [artifactFileName(k, V), k]));
    // Distinct names, so no installer can shadow the raw binary of its platform.
    expect(byFilename.size).toBe(keys.length);
    expect(byFilename.get('gaia-0.23.0-win-x64-setup.exe')).toBe('win-x64-setup');
    expect(byFilename.get('gaia-win-x64.exe')).toBe('win-x64');
  });
});

describe('INSTALLERS_FOR', () => {
  it('offers an installer for every platform the flagship agent builds for', () => {
    expect(INSTALLERS_FOR['win-x64']).toEqual(['win-x64-setup']);
    expect(INSTALLERS_FOR['darwin-arm64']).toEqual(['darwin-arm64-pkg']);
    expect(INSTALLERS_FOR['darwin-x64']).toEqual(['darwin-x64-pkg']);
  });

  // Not an oversight to fill in later: no agent build exists for either, so an
  // installer would set up a terminal with nothing behind it. The page keeps
  // handing these two the raw binary and says why.
  it('has no installer for the two arm64 platforms', () => {
    expect(INSTALLERS_FOR['win-arm64']).toEqual([]);
    expect(INSTALLERS_FOR['linux-arm64']).toEqual([]);
  });

  // A user agent does not report the distro, so the page asks instead of
  // guessing — half of Linux visitors would get a package they cannot open.
  it('offers both Linux packages side by side', () => {
    expect(INSTALLERS_FOR['linux-x64']).toEqual(['linux-x64-deb', 'linux-x64-rpm']);
  });

  it('labels every key it can render', () => {
    for (const platform of ORDER) {
      expect(DOWNLOAD_LABELS[platform]).toBeTruthy();
      for (const key of INSTALLERS_FOR[platform]) {
        expect(DOWNLOAD_LABELS[key]).toBeTruthy();
        expect(INSTALLER_CTA_LABELS[key]).toBeTruthy();
      }
    }
  });
});

// What TuiDownload.astro does with a manifest: resolve each key to its exact
// filename and look that up in what the hub published. Nothing is constructed
// from a guess, so an unpublished key is simply absent.
const resolveRows = (filenames: string[], version: string) => {
  const published = new Set(filenames);
  return ORDER.map((platform) => ({
    platform,
    binary: published.has(artifactFileName(platform, version)),
    installers: INSTALLERS_FOR[platform].filter((k) =>
      published.has(artifactFileName(k, version))
    ),
  }));
};

describe('resolving a manifest into download rows', () => {
  it('promotes the installer and keeps the binary when both are published', () => {
    const rows = resolveRows([...RAW_BINARIES, ...INSTALLER_FILES], V);
    const win = rows.find((r) => r.platform === 'win-x64')!;
    expect(win.installers).toEqual(['win-x64-setup']);
    expect(win.binary).toBe(true);

    const linux = rows.find((r) => r.platform === 'linux-x64')!;
    expect(linux.installers).toEqual(['linux-x64-deb', 'linux-x64-rpm']);
  });

  it('leaves the arm64 platforms on the raw binary', () => {
    const rows = resolveRows([...RAW_BINARIES, ...INSTALLER_FILES], V);
    for (const platform of ['win-arm64', 'linux-arm64'] as PlatformKey[]) {
      const row = rows.find((r) => r.platform === platform)!;
      expect(row.installers).toEqual([]);
      expect(row.binary).toBe(true);
    }
  });

  // The live state today: 0.23.0 publishes only the six raw binaries. That is a
  // legitimate release, not an error — the page renders as it did before.
  it('renders every platform from binaries alone when no installer is published', () => {
    const rows = resolveRows(RAW_BINARIES, V);
    expect(rows.every((r) => r.binary)).toBe(true);
    expect(rows.every((r) => r.installers.length === 0)).toBe(true);
  });

  // A release whose installer jobs partly failed still publishes the rest. The
  // page shows what exists rather than a link to a file that was never uploaded.
  it('drops only the installers the hub did not publish', () => {
    const rows = resolveRows(
      [...RAW_BINARIES, artifactFileName('linux-x64-deb', V)],
      V
    );
    expect(rows.find((r) => r.platform === 'linux-x64')!.installers).toEqual([
      'linux-x64-deb',
    ]);
    expect(rows.find((r) => r.platform === 'win-x64')!.installers).toEqual([]);
  });

  // The version in an installer filename is the manifest's, never a hardcoded
  // one — a stale version resolves to a name the hub never published.
  it('finds nothing when the manifest version does not match the filenames', () => {
    const rows = resolveRows([...RAW_BINARIES, ...INSTALLER_FILES], '0.24.0');
    expect(rows.every((r) => r.installers.length === 0)).toBe(true);
    expect(rows.every((r) => r.binary)).toBe(true);
  });
});

describe('PLATFORM_LABELS', () => {
  it('names every platform the page orders', () => {
    for (const platform of ORDER) expect(PLATFORM_LABELS[platform]).toBeTruthy();
  });
});
