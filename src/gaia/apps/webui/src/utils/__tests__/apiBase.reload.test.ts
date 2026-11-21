// @vitest-environment node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The desktop app loads the window with its backend's port in `?api=`. After
 * the backend restarts on a new port, a reload of the window must reach the
 * new port at once: the old one has nothing listening, and what the window
 * asks there before the app's restart news arrives fails (once, a cloud-only
 * install's reload landed in first-run setup that way).
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

const PAGE = 'file:///C:/GAIA/resources/dist/index.html?api=http%3A%2F%2F127.0.0.1%3A52778%2Fapi#9d2ceb5';

/** A desktop window's page: its URL, and the history that can rewrite it. */
function desktopPage(href: string) {
    const page = { location: new URL(href) };
    const history = {
        state: { idx: 3 },
        replaceState: vi.fn((_state: unknown, _title: string, url: string | URL) => {
            page.location = new URL(String(url), page.location.href);
        }),
    };
    vi.stubGlobal('window', {
        get location() { return page.location; },
        history,
    });
    return { page, history };
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
});

describe('a backend restarted on a new port', () => {
    it('is the base a reload of the window starts from', async () => {
        const { page, history } = desktopPage(PAGE);
        const before = await import('../apiBase');
        expect(before.getApiBase()).toBe('http://127.0.0.1:52778/api');

        before.setApiBase('http://127.0.0.1:62617/api');

        // A reload is a fresh module over the page's URL as it now reads.
        vi.resetModules();
        const reloaded = await import('../apiBase');
        expect(reloaded.getApiBase()).toBe('http://127.0.0.1:62617/api');
        // The chat the window showed, and the history entry's state, stay.
        expect(page.location.hash).toBe('#9d2ceb5');
        expect(history.replaceState).toHaveBeenCalledWith({ idx: 3 }, '', expect.anything());
    });

    it('never writes an untrusted base into the URL', async () => {
        const { page, history } = desktopPage(PAGE);
        const { setApiBase } = await import('../apiBase');
        expect(() => setApiBase('http://evil.example:80/api')).toThrow(/loopback/);
        expect(history.replaceState).not.toHaveBeenCalled();
        expect(page.location.href).toBe(PAGE);
    });

    it('leaves a page the backend serves itself alone', async () => {
        const { history } = desktopPage('http://127.0.0.1:4200/#abc');
        const { setApiBase } = await import('../apiBase');
        setApiBase('http://127.0.0.1:62617/api');
        expect(history.replaceState).not.toHaveBeenCalled();
    });
});
