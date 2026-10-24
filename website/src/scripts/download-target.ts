// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Which published download this visitor's machine can run, and what the GitHub
// release calls the file. Two surfaces ship: the terminal hub and the Agent UI
// desktop app, and their platform coverage is NOT the same — see DESKTOP_PLATFORMS.
//
// The terminal hub publishes six: {win,darwin,linux} x {x64,arm64}, so unlike a
// single-architecture installer this has to resolve the ARCHITECTURE too, and
// only the client hints report it truthfully — every OS masks arm64 in the UA
// string to keep old sites working. Nothing here guesses: a machine we cannot
// place resolves to no platform at all and the caller keeps its full list on
// screen, and a Mac — whose architecture Safari never reports — resolves to
// both builds, for the caller to offer side by side. An arm64 user handed an
// x64 binary gets a failure at exec time, not a download error they can act on.

export type Os = 'win' | 'darwin' | 'linux';
export type Arch = 'x64' | 'arm64';
/** A platform the site offers a download for, e.g. `win-x64`. */
export type PlatformKey = `${Os}-${Arch}`;

/**
 * The native installers, keyed by the hub publish keys that
 * installer/tui/artifacts.py assigns — deliberately distinct from the six raw ones.
 */
export type InstallerKey =
  | 'win-x64-setup'
  | 'darwin-arm64-pkg'
  | 'darwin-x64-pkg'
  | 'linux-x64-deb'
  | 'linux-x64-rpm';

/** Anything the site can offer: a raw binary, or an installer for one. */
export type DownloadKey = PlatformKey | InstallerKey;

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

/**
 * Reading order for every platform list on the site: the common machines
 * first, then the three the desktop app does not build for. Shared so the two
 * surfaces list their platforms in the same order and a visitor scanning for
 * their own machine finds it in the same place twice.
 */
export const PLATFORM_ORDER: readonly PlatformKey[] = [
  'win-x64',
  'darwin-arm64',
  'linux-x64',
  'win-arm64',
  'darwin-x64',
  'linux-arm64',
];

/**
 * Which installers exist for each platform.
 *
 * win-arm64 and linux-arm64 are EMPTY on purpose, not pending: the flagship
 * agent publishes no build for either, so an installer would set up a terminal
 * front end with no agent behind it. Those two keep the raw binary, and the
 * page says so rather than implying an installer is coming.
 *
 * linux-x64 lists both packages because a user agent reports no distro — .deb
 * and .rpm are offered side by side, since asking beats a coin flip that leaves
 * half of Linux visitors with a package their machine cannot open.
 */
export const INSTALLERS_FOR: Record<PlatformKey, InstallerKey[]> = {
  'win-x64': ['win-x64-setup'],
  'win-arm64': [],
  'darwin-x64': ['darwin-x64-pkg'],
  'darwin-arm64': ['darwin-arm64-pkg'],
  'linux-x64': ['linux-x64-deb', 'linux-x64-rpm'],
  'linux-arm64': [],
};

/**
 * The first release whose Windows setup installs the desktop app, the terminal,
 * or both, chosen while it runs.
 *
 * It keeps the terminal hub's `win-x64-setup` filename, which earlier releases
 * used for a terminal-only setup -- so the file alone cannot tell the two
 * apart, and the version has to. Offering the choice for an older version would
 * send Windows visitors to a setup that has no desktop app in it.
 */
export const WINDOWS_SETUP_SINCE = '0.25.0';

/**
 * The Windows one-liner, shared by the install-command block and the Windows
 * setup's "terminal app only" option so the two cannot drift. Keep it
 * byte-identical to docs/quickstart.mdx.
 */
export const WINDOWS_INSTALL_COMMAND = 'irm https://amd-gaia.ai/install.ps1 | iex';

/** True when `version` is at or past WINDOWS_SETUP_SINCE; false if unparseable. */
export function windowsSetupOffersChoice(version: string): boolean {
  const core = (v: string) => v.split('-')[0].split('.').map(Number);
  const have = core(version);
  const need = core(WINDOWS_SETUP_SINCE);
  if (have.length !== 3 || have.some((n) => !Number.isInteger(n))) return false;
  for (let i = 0; i < 3; i++) {
    if (have[i] !== need[i]) return have[i] > need[i];
  }
  return true;
}

/**
 * Primary-button text per installer. Linux names the distro family in the
 * button itself: the two packages sit side by side and the visitor picks, so
 * the button has to say which one is theirs. macOS names the chip for the same
 * reason — Safari cannot report it, so both Mac buttons are shown together and
 * a bare "Install GAIA for macOS" beside "(Intel)" would read as the universal
 * one.
 */
export const INSTALLER_CTA_LABELS: Record<InstallerKey, string> = {
  'win-x64-setup': 'Install GAIA for Windows',
  'darwin-arm64-pkg': 'Install GAIA for macOS (Apple Silicon)',
  'darwin-x64-pkg': 'Install GAIA for macOS (Intel)',
  'linux-x64-deb': 'Debian / Ubuntu (.deb)',
  'linux-x64-rpm': 'Fedora / RHEL / SUSE (.rpm)',
};

/** What the file IS, for a list already grouped under its platform heading. */
export const DOWNLOAD_LABELS: Record<DownloadKey, string> = {
  'win-x64-setup': 'Installer (.exe)',
  'darwin-arm64-pkg': 'Installer (.pkg)',
  'darwin-x64-pkg': 'Installer (.pkg)',
  'linux-x64-deb': 'Installer (.deb — Debian, Ubuntu)',
  'linux-x64-rpm': 'Installer (.rpm — Fedora, RHEL, SUSE)',
  'win-x64': 'Standalone binary',
  'win-arm64': 'Standalone binary',
  'darwin-x64': 'Standalone binary',
  'darwin-arm64': 'Standalone binary',
  'linux-x64': 'Standalone binary',
  'linux-arm64': 'Standalone binary',
};

export function detectOs(userAgent: string): Os | null {
  if (/Windows/i.test(userAgent)) return 'win';
  if (/Mac/i.test(userAgent) && !/iPhone|iPad|iPod/i.test(userAgent)) return 'darwin';
  if (/Linux/i.test(userAgent) && !/Android/i.test(userAgent)) return 'linux';
  return null;
}

/**
 * iPadOS Safari asks for desktop sites by default, sending the *Mac* UA
 * (`Macintosh; Intel Mac OS X 10_15_7`) with no iPad token — so the UA alone
 * calls it a Mac. Touch points are the only separator: no Mac has a
 * touchscreen and every one reports 0 (Touch Bar models included), while an
 * iPad reports 5. `> 1` rather than `> 0` because a handful of browsers report
 * 1 on non-touch hardware.
 */
function isIpadClaimingToBeAMac(nav: NavigatorLike): boolean {
  return /Macintosh/i.test(nav.userAgent) && (nav.maxTouchPoints ?? 0) > 1;
}

/** The OS we publish for, or null for one we do not — including an iPad. */
export function placeOs(nav: NavigatorLike): Os | null {
  if (isIpadClaimingToBeAMac(nav)) return null;
  return detectOs(nav.userAgent);
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
  // Undecidable on macOS: Safari exposes no client hints, and every Mac —
  // Intel and Apple Silicon alike — says "Intel Mac OS X". Nothing is left to
  // read, so callers offer both builds rather than guess.
  if (os === 'darwin') return null;
  // Windows and Linux on arm64 without hints are rare enough that x64 is the
  // safe read; on macOS it is not, which is why that case returns null above.
  if (/x86_64|win64|wow64|x64|amd64|intel/i.test(nav.userAgent)) return 'x64';
  return null;
}

/**
 * Every platform to offer this visitor, best first.
 *
 * Usually one, and empty for a machine we do not publish for. Two only on a
 * Mac whose architecture cannot be read — Apple Silicon leads because it is
 * what Apple has shipped since 2020. Offering both beats the alternatives: a
 * guess hands half of Safari's Mac visitors a package that will not run, and
 * offering nothing is what left them with no install button at all.
 *
 * This replaced a `resolvePlatform` that returned one platform or null. The
 * null was indistinguishable from "unsupported machine", so the caller showed
 * no install button at all — which is how a Mac on Safari ended up with none.
 */
export async function resolveTargets(nav: NavigatorLike): Promise<PlatformKey[]> {
  const os = placeOs(nav);
  if (!os) return [];
  const arch = await resolveArch(nav, os);
  if (arch) return [`${os}-${arch}`];
  return os === 'darwin' ? ['darwin-arm64', 'darwin-x64'] : [];
}

/**
 * The header Install button's download for a visitor, or null to keep it on
 * the install section. Only a machine placed on exactly ONE platform gets a
 * file: a Mac of unknown architecture resolves to two, an unplaceable machine
 * to none, and a platform with no desktop build on this release has no entry
 * -- each keeps the section, where every option shows.
 */
export function pickInstallTarget<T>(
  targets: readonly PlatformKey[],
  byPlatform: Partial<Record<PlatformKey, T>>,
): T | null {
  return targets.length === 1 ? (byPlatform[targets[0]] ?? null) : null;
}

// The raw terminal binaries as publish.yml attaches them to the GitHub release:
// build_tui.yml names them `gaia-<goos>-<goarch>`, so Go's names (windows,
// amd64), not the site's keys (win, x64). Spelled out per key rather than
// translated, so a key with no published name cannot produce one.
const BINARY_FILENAMES: Record<PlatformKey, string> = {
  'win-x64': 'gaia-windows-amd64.exe',
  'win-arm64': 'gaia-windows-arm64.exe',
  'darwin-x64': 'gaia-darwin-amd64',
  'darwin-arm64': 'gaia-darwin-arm64',
  'linux-x64': 'gaia-linux-amd64',
  'linux-arm64': 'gaia-linux-arm64',
};

// The installer filenames, as installer/tui/artifacts.py names them — each
// carries the version, and each follows its own platform's convention rather
// than one house style (dpkg and rpm both parse the name to get the version out).
const INSTALLER_FILENAMES: Record<InstallerKey, (version: string) => string> = {
  'win-x64-setup': (v) => `gaia-${v}-win-x64-setup.exe`,
  'darwin-arm64-pkg': (v) => `gaia-${v}-darwin-arm64.pkg`,
  'darwin-x64-pkg': (v) => `gaia-${v}-darwin-x64.pkg`,
  'linux-x64-deb': (v) => `gaia_${v}_amd64.deb`,
  'linux-x64-rpm': (v) => `gaia-${v}.x86_64.rpm`,
};

/**
 * The GitHub release asset name for a download key, e.g. `win-x64` ->
 * `gaia-windows-amd64.exe`, `win-x64-setup` -> `gaia-0.23.0-win-x64-setup.exe`.
 *
 * The site resolves key -> asset name and looks that up in the assets the
 * release actually carries, never asset name -> key: a release also carries
 * wheels, blockmaps and checksum files, and parsing a platform back out of a
 * name that carries a version silently drops assets. A key with no matching
 * asset is simply not published — the caller renders what exists and invents
 * nothing.
 */
export function artifactFileName(key: DownloadKey, version: string): string {
  const installer = INSTALLER_FILENAMES[key as InstallerKey];
  if (installer) return installer(version);
  return BINARY_FILENAMES[key as PlatformKey];
}

// ---- The Agent UI desktop app ----

// One asset per platform, each named by its own platform's packaging
// convention rather than a house style — these are what electron-builder emits
// and what publish.yml attaches to the release, not a scheme this file chose.
const DESKTOP_FILENAMES: Partial<Record<PlatformKey, (version: string) => string>> = {
  'win-x64': (v) => `gaia-agent-ui-${v}-x64-setup.exe`,
  'darwin-arm64': (v) => `gaia-agent-ui-${v}-arm64.dmg`,
  'linux-x64': (v) => `gaia-agent-ui-${v}-x86_64.AppImage`,
};

/**
 * The three platforms the desktop app builds for.
 *
 * Deliberately NOT the six the terminal covers. The app builds for win-x64,
 * linux-x64 and darwin-arm64 and nothing else, so an Intel
 * Mac, a Windows ARM64 machine and an ARM64 Linux box have no desktop build at
 * all. The site says that plainly — see DESKTOP_MISSING_PLATFORMS — instead of
 * showing an option that 404s or wording it as one that is on the way.
 */
export const DESKTOP_PLATFORMS: readonly PlatformKey[] = PLATFORM_ORDER.filter(
  (p) => p in DESKTOP_FILENAMES,
);

/** The three with no desktop build, for the copy that has to name them. */
export const DESKTOP_MISSING_PLATFORMS: readonly PlatformKey[] = PLATFORM_ORDER.filter(
  (p) => !(p in DESKTOP_FILENAMES),
);

/** What the desktop file IS, under a heading that already names the platform. */
export const DESKTOP_DOWNLOAD_LABELS: Partial<Record<PlatformKey, string>> = {
  'win-x64': 'Installer (.exe)',
  'darwin-arm64': 'Disk image (.dmg)',
  'linux-x64': 'AppImage',
};

/**
 * The desktop app's release asset name for a platform, or null where it
 * publishes none. Same key -> filename -> published-artifact direction as
 * artifactFileName: null here means the caller renders no desktop option,
 * never a constructed URL.
 */
export function desktopArtifactFileName(
  platform: PlatformKey,
  version: string,
): string | null {
  return DESKTOP_FILENAMES[platform]?.(version) ?? null;
}

/**
 * Other packagings of the desktop app, offered in the full download list
 * rather than as a platform's button. Linux's AppImage runs on any distro, so
 * it is the button; the .deb is for a visitor who wants the package manager.
 */
const DESKTOP_EXTRA_FILENAMES: Partial<
  Record<PlatformKey, { label: string; name: (version: string) => string }[]>
> = {
  'linux-x64': [
    { label: '.deb (Debian, Ubuntu)', name: (v) => `gaia-agent-ui-${v}-amd64.deb` },
  ],
};

/** The extra desktop packagings for a platform, as [label, asset name] pairs. */
export function desktopExtraFileNames(
  platform: PlatformKey,
  version: string,
): { label: string; name: string }[] {
  return (DESKTOP_EXTRA_FILENAMES[platform] ?? []).map((e) => ({
    label: e.label,
    name: e.name(version),
  }));
}
