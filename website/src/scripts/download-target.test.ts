// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';

import {
  artifactName,
  detectOs,
  resolveArch,
  resolvePlatform,
  type NavigatorLike,
} from './download-target';

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

  // Safari reports "Intel Mac OS X" on Apple Silicon and offers no hints, so
  // the UA alone cannot decide; touch points are the one honest signal.
  it('uses touch points to place an Apple Silicon Mac behind Safari', async () => {
    expect(await resolveArch(nav(UA.macSafari, { maxTouchPoints: 5 }), 'darwin')).toBe('arm64');
  });

  it('refuses to guess a Mac architecture with no evidence', async () => {
    expect(await resolveArch(nav(UA.macSafari), 'darwin')).toBeNull();
    expect(await resolveArch(nav(UA.mac, { hintsThrow: true }), 'darwin')).toBeNull();
  });
});

describe('resolvePlatform', () => {
  it('returns the hub platform key', async () => {
    expect(await resolvePlatform(nav(UA.win))).toBe('win-x64');
    expect(await resolvePlatform(nav(UA.linuxArm))).toBe('linux-arm64');
    expect(await resolvePlatform(nav(UA.mac, { architecture: 'arm' }))).toBe('darwin-arm64');
  });

  it('returns null for anything unplaceable, so the caller shows every option', async () => {
    expect(await resolvePlatform(nav(UA.iphone))).toBeNull();
    expect(await resolvePlatform(nav(UA.macSafari))).toBeNull();
  });
});

describe('artifactName', () => {
  // These are the exact filenames release_components.yml publishes; a wrong one
  // is not a build failure anywhere, it is a 404 on the visitor's click.
  it('matches the hub filenames, .exe on Windows only', () => {
    expect(artifactName('win-x64')).toBe('gaia-win-x64.exe');
    expect(artifactName('win-arm64')).toBe('gaia-win-arm64.exe');
    expect(artifactName('darwin-arm64')).toBe('gaia-darwin-arm64');
    expect(artifactName('linux-x64')).toBe('gaia-linux-x64');
  });
});
