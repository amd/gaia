// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Which published terminal-hub download this visitor's machine can run, and
// what the hub calls the file.
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

/**
 * The native installers, published under platform keys deliberately distinct
 * from the six raw ones so a hub install — which downloads the key matching the
 * machine — can never be handed a setup file where it expects a bare binary.
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

// The installer filenames, as the packaging scripts name them — each carries
// the version, and each follows its own platform's convention rather than one
// house style (dpkg and rpm both parse the name to get the version out).
const INSTALLER_FILENAMES: Record<InstallerKey, (version: string) => string> = {
  'win-x64-setup': (v) => `gaia-${v}-win-x64-setup.exe`,
  'darwin-arm64-pkg': (v) => `gaia-${v}-darwin-arm64.pkg`,
  'darwin-x64-pkg': (v) => `gaia-${v}-darwin-x64.pkg`,
  'linux-x64-deb': (v) => `gaia_${v}_amd64.deb`,
  'linux-x64-rpm': (v) => `gaia-${v}.x86_64.rpm`,
};

/**
 * The hub's artifact filename for a download key, e.g. `win-x64` ->
 * `gaia-win-x64.exe`, `win-x64-setup` -> `gaia-0.23.0-win-x64-setup.exe`.
 *
 * The site resolves key -> filename and looks that up in what the hub actually
 * published, never filename -> key: the manifest records no platform field, and
 * parsing it back out of a name that carries a version silently drops
 * artifacts. A key with no matching artifact is simply not published — the
 * caller renders what exists and invents nothing.
 */
export function artifactFileName(key: DownloadKey, version: string): string {
  const installer = INSTALLER_FILENAMES[key as InstallerKey];
  if (installer) return installer(version);
  return key.startsWith('win-') ? `gaia-${key}.exe` : `gaia-${key}`;
}
