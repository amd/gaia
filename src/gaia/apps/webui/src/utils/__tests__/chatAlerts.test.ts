// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import {
    alertText,
    attentionCount,
    batteryAllowsKeepAwake,
    chatAlertEvents,
    chatIsInView,
    chatPhases,
    ordinal,
} from '../chatAlerts';
import { DEFAULT_ALERT_SETTINGS } from '../../stores/notificationStore';
import type { Session } from '../../types';

function chat(id: string, extra: Partial<Session> = {}): Session {
    return {
        id, title: `Chat ${id}`, created_at: '', updated_at: '', model: '', system_prompt: null,
        message_count: 0, document_ids: [], kind: 'chat', ...extra,
    };
}

describe('chatPhases', () => {
    it('puts a pause on the user over plain running', () => {
        expect(chatPhases(['a', 'b', 'c'], { b: 'question' }, ['c'])).toEqual({
            a: 'running', b: 'question', c: 'permission',
        });
    });
});

describe('chatAlertEvents', () => {
    it('fires once when a chat starts waiting on the user, and once when its turn ends', () => {
        expect(chatAlertEvents({}, { a: 'running' })).toEqual([]);
        expect(chatAlertEvents({ a: 'running' }, { a: 'permission' }))
            .toEqual([{ sessionId: 'a', event: 'needs_approval' }]);
        expect(chatAlertEvents({ a: 'permission' }, { a: 'permission' })).toEqual([]);
        expect(chatAlertEvents({ a: 'running' }, { a: 'question' }))
            .toEqual([{ sessionId: 'a', event: 'question' }]);
        expect(chatAlertEvents({ a: 'question' }, {})).toEqual([{ sessionId: 'a', event: 'finished' }]);
    });
});

describe('alertText', () => {
    it('names the chat and says when it failed', () => {
        expect(alertText('needs_approval', chat('a'))).toEqual({ title: 'GAIA needs your approval', body: 'Chat a' });
        expect(alertText('finished', chat('a', { last_run_status: 'failed' })).title).toBe('GAIA hit a problem');
        expect(alertText('finished', undefined).body).toBe('A chat');
        expect(alertText('finished', chat('a', { last_run_status: 'done' })).title).toBe('GAIA finished');
        expect(alertText('finished', chat('a', { last_run_status: 'stopped' })).title).toBe('GAIA stopped');
        // Unknown outcome: no claim that it worked.
        expect(alertText('finished', chat('a')).title).toBe('A chat stopped running');
    });
});

describe('chatIsInView', () => {
    it('is in view only when focused on that chat', () => {
        expect(chatIsInView('a', 'a', true)).toBe(true);
        expect(chatIsInView('a', 'a', false)).toBe(false);
        expect(chatIsInView('a', 'b', true)).toBe(false);
    });
});

describe('attentionCount', () => {
    const sessions = [
        chat('a'), chat('b'), chat('c', { unread: true }), chat('d', { unread: true }),
        chat('e', { unread: true, kind: 'eval' }),
    ];
    const phases = { a: 'permission', b: 'question' } as const;

    it('counts chats paused on you and unread finished chats, skipping the open one and automated runs', () => {
        expect(attentionCount(sessions, phases, DEFAULT_ALERT_SETTINGS.modes, 'd')).toBe(3);
    });

    it('leaves out each event that is turned off', () => {
        const modes = { needs_approval: 'off', question: 'badge', finished: 'off' } as const;
        expect(attentionCount(sessions, phases, modes, null)).toBe(1);
    });
});

describe('batteryAllowsKeepAwake', () => {
    it('lets the PC sleep only on battery below the floor', () => {
        expect(batteryAllowsKeepAwake(null, 20)).toBe(true);
        expect(batteryAllowsKeepAwake({ charging: true, level: 0.05 }, 20)).toBe(true);
        expect(batteryAllowsKeepAwake({ charging: false, level: 0.5 }, 20)).toBe(true);
        expect(batteryAllowsKeepAwake({ charging: false, level: 0.19 }, 20)).toBe(false);
        expect(batteryAllowsKeepAwake({ charging: false, level: 0.01 }, 0)).toBe(true);
    });
});

describe('ordinal', () => {
    it.each([[1, '1st'], [2, '2nd'], [3, '3rd'], [4, '4th'], [11, '11th'], [12, '12th'], [21, '21st'], [113, '113th']])(
        '%i → %s', (n, s) => expect(ordinal(n)).toBe(s),
    );
});
