// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { resolveTheme } from '../theme';

const html = readFileSync(resolve(__dirname, '../../../index.html'), 'utf8');
const inline = html.match(/<script>([\s\S]*?)<\/script>/)![1];

function osPrefers(scheme: 'light' | 'dark') {
    window.matchMedia = ((q: string) => ({
        matches: q.includes('light') ? scheme === 'light' : scheme === 'dark',
    })) as unknown as typeof window.matchMedia;
}

function firstPaintTheme(saved: string | null, os: 'light' | 'dark') {
    document.documentElement.removeAttribute('data-theme');
    if (saved === null) localStorage.removeItem('gaia-chat-theme');
    else localStorage.setItem('gaia-chat-theme', saved);
    osPrefers(os);
    new Function(inline)();
    return document.documentElement.getAttribute('data-theme');
}

describe('theme painted before the app loads', () => {
    afterEach(() => localStorage.clear());

    it('a saved dark theme is on the page before any bundle runs, even with a light OS', () => {
        expect(firstPaintTheme('dark', 'light')).toBe('dark');
    });

    it('agrees with the app for every saved value and OS setting', () => {
        for (const saved of [null, 'light', 'dark', 'system', 'garbage']) {
            for (const os of ['light', 'dark'] as const) {
                const pref = saved === 'light' || saved === 'dark' ? saved : 'system';
                osPrefers(os);
                expect(firstPaintTheme(saved, os), `${saved} / OS ${os}`).toBe(resolveTheme(pref));
            }
        }
    });
});
