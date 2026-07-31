// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import {
  detectOS,
  resolveDownloadTarget,
  TARGET_ASSETS,
  type NavigatorLike,
} from './download-target';

const UA = {
  win: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  macSafari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  linux: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  ipad: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  android: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36',
};

const nav = (userAgent: string, architecture?: string | Error): NavigatorLike => ({
  userAgent,
  userAgentData:
    architecture === undefined
      ? undefined
      : {
          getHighEntropyValues: async () => {
            if (architecture instanceof Error) throw architecture;
            return { architecture };
          },
        },
});

describe('detectOS', () => {
  it('reads the OS off the user agent', () => {
    expect(detectOS(UA.win)).toBe('windows');
    expect(detectOS(UA.mac)).toBe('macos');
    expect(detectOS(UA.linux)).toBe('linux');
  });

  it('does not treat iPadOS as macOS or Android as Linux', () => {
    expect(detectOS(UA.ipad)).toBeNull();
    expect(detectOS(UA.android)).toBeNull();
  });
});

describe('resolveDownloadTarget', () => {
  it('resolves Windows and Linux without needing client hints', async () => {
    await expect(resolveDownloadTarget(nav(UA.win))).resolves.toBe('windows');
    await expect(resolveDownloadTarget(nav(UA.linux))).resolves.toBe('linux');
  });

  it('resolves a Mac only when the hints confirm Apple Silicon', async () => {
    await expect(resolveDownloadTarget(nav(UA.mac, 'arm'))).resolves.toBe('macos-arm64');
  });

  it('refuses an Intel Mac — there is no darwin-x64 build', async () => {
    await expect(resolveDownloadTarget(nav(UA.mac, 'x86'))).resolves.toBeNull();
  });

  it('refuses a Mac whose architecture cannot be read', async () => {
    // Safari and Firefox expose no userAgentData, and Chromium masks the arch in
    // the UA string, so the CTA must keep its releases-page fallback.
    await expect(resolveDownloadTarget(nav(UA.macSafari))).resolves.toBeNull();
    await expect(resolveDownloadTarget(nav(UA.mac, new Error('refused')))).resolves.toBeNull();
  });

  it('resolves nothing for an unknown platform', async () => {
    await expect(resolveDownloadTarget(nav(UA.ipad, 'arm'))).resolves.toBeNull();
  });
});

describe('TARGET_ASSETS', () => {
  // The v0.22.0 release asset list, verbatim.
  const assets = [
    'gaia-agent-ui-0.22.0-amd64.deb',
    'gaia-agent-ui-0.22.0-arm64.dmg',
    'gaia-agent-ui-0.22.0-arm64.dmg.blockmap',
    'gaia-agent-ui-0.22.0-x64-setup.exe',
    'gaia-agent-ui-0.22.0-x64-setup.exe.blockmap',
    'gaia-agent-ui-0.22.0-x86_64.AppImage',
  ];

  it('picks one real installer per target and never a blockmap', () => {
    const pick = (re: RegExp) => assets.filter((a) => re.test(a));
    expect(pick(TARGET_ASSETS.windows)).toEqual(['gaia-agent-ui-0.22.0-x64-setup.exe']);
    expect(pick(TARGET_ASSETS['macos-arm64'])).toEqual(['gaia-agent-ui-0.22.0-arm64.dmg']);
    expect(pick(TARGET_ASSETS.linux)).toEqual(['gaia-agent-ui-0.22.0-x86_64.AppImage']);
  });
});
