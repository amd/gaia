// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { isPathAccessFollowUp, pathAccessQuestion, pathAccessTitle } from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

describe('pathAccessQuestion', () => {
    it('asks about a file as a file', () => {
        expect(pathAccessQuestion({ path: 'C:\\Users\\me\\sales_data_2025.csv', kind: 'file' })).toBe(
            "GAIA wants to use the file C:\\Users\\me\\sales_data_2025.csv. This chat can't reach it yet. Allow it for this chat?"
        );
    });

    it('says a folder grant covers what is in it', () => {
        expect(pathAccessQuestion({ path: 'C:\\Users\\me\\reports', kind: 'folder' })).toContain(
            'the folder C:\\Users\\me\\reports and everything in it'
        );
    });

    it('says the target does not exist yet when the agent sent no kind', () => {
        expect(pathAccessQuestion({ path: 'C:\\Users\\me\\new.txt' })).toBe(
            "GAIA wants to use C:\\Users\\me\\new.txt, which doesn't exist yet. This chat can't reach it yet. Allow it for this chat?"
        );
    });

    it('reads as a follow-up to the call just allowed', () => {
        expect(pathAccessQuestion({ path: 'C:\\h\\a.txt', kind: 'file' }, true)).toBe(
            "To do what you just allowed, GAIA also needs the file C:\\h\\a.txt. This chat can't reach it yet. Allow it for this chat?"
        );
    });
});

describe('pathAccessTitle', () => {
    it.each([
        [{ kind: 'file' }, false, 'Let GAIA use this file?'],
        [{ kind: 'folder' }, false, 'Let GAIA use this folder?'],
        [{}, false, 'Let GAIA use this location?'],
        [{ kind: 'file' }, true, 'Also let GAIA use this file?'],
    ])('titles %o (follow-up %s) as %s', (args, followUp, title) => {
        expect(pathAccessTitle({ path: 'C:\\x', ...args }, followUp)).toBe(title);
    });

    it('never names the internal tool', () => {
        expect(pathAccessTitle({ path: 'C:\\x' })).not.toContain('allow_path_access');
    });
});

function prompt(over: Partial<GaiaNotification>): GaiaNotification {
    return {
        id: 'p', type: 'permission_request', agentId: 's', sessionId: 's', agentName: 'GAIA',
        title: '', message: '', timestamp: 1, read: true, dismissed: false, priority: 'high',
        tool: 'write_file', toolArgs: { file_path: 'C:\\Users\\me\\home\\gaia_ui_test.txt' },
        response: 'allow', ...over,
    };
}

describe('isPathAccessFollowUp', () => {
    const path = { path: 'C:\\Users\\me\\home\\gaia_ui_test.txt', kind: 'file' };

    it('follows a call allowed in this chat on the same path', () => {
        expect(isPathAccessFollowUp([prompt({})], 's', path)).toBe(true);
    });

    it('matches across slash style and drive-letter case', () => {
        const n = prompt({ toolArgs: { file_path: 'c:/users/me/home/gaia_ui_test.txt' } });
        expect(isPathAccessFollowUp([n], 's', path)).toBe(true);
    });

    it('matches a folder that holds the allowed call target', () => {
        expect(isPathAccessFollowUp([prompt({})], 's', { path: 'C:\\Users\\me\\home', kind: 'folder' })).toBe(true);
    });

    it('matches a relative argument by its trailing segments', () => {
        expect(isPathAccessFollowUp([prompt({ toolArgs: { file_path: 'home/gaia_ui_test.txt' } })], 's', path)).toBe(true);
    });

    it.each([
        ['a denied call', prompt({ response: 'deny' })],
        ['an unanswered call', prompt({ response: undefined })],
        ['another chat', prompt({ sessionId: 'other' })],
        ['a different path', prompt({ toolArgs: { file_path: 'C:\\Users\\me\\other.txt' } })],
        ['an earlier path prompt', prompt({ tool: 'allow_path_access', toolArgs: path })],
    ])('does not follow %s', (_label, n) => {
        expect(isPathAccessFollowUp([n], 's', path)).toBe(false);
    });

    it('only looks at the chat\'s most recent prompt', () => {
        const older = prompt({ id: 'older' });
        const newer = prompt({ id: 'newer', toolArgs: { file_path: 'C:\\elsewhere.txt' } });
        expect(isPathAccessFollowUp([newer, older], 's', path)).toBe(false);
    });
});
