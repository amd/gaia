// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A blocked localStorage must say so.
 *
 * The preference read/write used to be wrapped in `catch { }`, so in private
 * browsing every UI setting silently reset on reload with nothing in the
 * console to explain it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Replace the storage with one that throws, the way a blocked origin behaves. */
function breakStorage(message: string) {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException(message, 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new DOMException(message, 'SecurityError');
    });
}

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
});

const warnings = () => warn.mock.calls.map((c: unknown[]) => c.join(' ')).join('\n');

describe('UI preferences survive a blocked localStorage -- loudly', () => {
    it('warns, and still updates state, when the write is refused', async () => {
        const { useChatStore } = await import('../chatStore');
        breakStorage('write denied');

        useChatStore.getState().setActiveAgentId('email');

        expect(useChatStore.getState().activeAgentId).toBe('email');
        expect(warn, 'a refused preference write was swallowed silently').toHaveBeenCalled();
        expect(warnings()).toMatch(/gaia-active-agent-id/);
        expect(warnings(), 'the warning must say what the user loses').toMatch(/reset on reload/);
    });

    it('warns, and falls back to the default, when the read is refused', async () => {
        breakStorage('read denied');
        vi.resetModules();

        const { useChatStore } = await import('../chatStore');

        expect(useChatStore.getState().activeAgentId).toBe('chat');
        expect(warn, 'an unreadable preference was swallowed silently').toHaveBeenCalled();
        expect(warnings()).toMatch(/unreadable/);
    });

    it('names the cause so the user can act on it', async () => {
        const { useChatStore } = await import('../chatStore');
        breakStorage('write denied');

        useChatStore.getState().setSidebarWidth(420);

        expect(warnings()).toMatch(/private-browsing|site-data/);
    });
});
