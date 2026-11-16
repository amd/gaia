// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { translate } from '@webui/i18n/registry';

import { createFakeClient } from '@/api/fake';
import type { Session } from '@/api/client.types';

import { buildChatSections, chatListState, msUntilNextMidnight, NO_LIVE_RUNS, rowText, type ChatRow } from './chatList';

const t = (key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) => translate('en', key, params);

const NOW = new Date(2026, 10, 12, 12, 0, 0);
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();

function session(id: string, over: Partial<Session> = {}): Session {
    return {
        id,
        title: `Chat ${id}`,
        created_at: hoursAgo(100),
        updated_at: hoursAgo(1),
        model: 'm',
        system_prompt: null,
        message_count: 1,
        document_ids: [],
        ...over,
    };
}

async function listed(sessions: Session[]) {
    return (await createFakeClient({ sessions }).listSessions()).sessions;
}

describe('buildChatSections', () => {
    it('puts Needs you first, then Pinned, then recency buckets, each chat once', async () => {
        const sessions = await listed([
            session('old', { updated_at: hoursAgo(24 * 40) }),
            session('today'),
            session('pin', { pinned: true, updated_at: hoursAgo(2) }),
            session('ask', { updated_at: hoursAgo(3), pinned: true }),
            session('yday', { updated_at: hoursAgo(30) }),
            session('bg', { kind: 'eval' }),
        ]);
        const live = { ...NO_LIVE_RUNS, awaiting: { ask: {} } };
        const sections = buildChatSections({ sessions, live, now: NOW });
        expect(sections.map((s) => [s.key, s.data.map((r) => r.id)])).toEqual([
            ['Needs you', ['ask']],
            ['Pinned', ['pin']],
            ['Today', ['today']],
            ['Yesterday', ['yday']],
            ['Older', ['old']],
        ]);
        expect(sections[0].titleKey).toBe('sidebar.group.needsYou');
    });

    it('hides a read done dot but keeps failed and unread-done ones', () => {
        const sessions = [
            session('read', { last_run_status: 'done' }),
            session('news', { last_run_status: 'done', unread: true }),
            session('bad', { last_run_status: 'failed' }),
            session('open', { last_run_status: 'done', unread: true }),
        ];
        const rows = Object.fromEntries(
            buildChatSections({ sessions, activeId: 'open', now: NOW })
                .flatMap((s) => s.data)
                .map((r) => [r.id, r]),
        );
        expect(rows.read.dot).toBeNull();
        expect(rows.news).toMatchObject({ dot: 'done', unread: true });
        expect(rows.bad.dot).toBe('failed');
        expect(rows.open).toMatchObject({ unread: false, active: true, dot: null });
    });

    it('reports place in line and unsent counts from the queues', () => {
        const live = { ...NO_LIVE_RUNS, queued: ['x', 'a'] };
        const [section] = buildChatSections({
            sessions: [session('a', { private: true })],
            live,
            queues: { a: [{ state: 'queued' }, { state: 'failed' }] },
            now: NOW,
        });
        expect(section.data[0]).toMatchObject({ dot: 'queued', place: 2, private: true, unsent: 2, failed: 1 });
    });
});

describe('rowText', () => {
    const base: ChatRow = { id: 'a', title: 'Taxes', active: false, unread: false, dot: null, place: 0, private: false, pinned: false, unsent: 0, failed: 0 };

    it('joins title, status, note and unread into the aria label', () => {
        const text = rowText({ ...base, dot: 'queued', place: 2, unsent: 3, unread: true }, t);
        expect(text.status).toBe('Waiting for the GPU, #2 in line');
        expect(text.note).toBe('#2 in line · 3 to send');
        expect(text.noteFailed).toBe(false);
        expect(text.aria).toBe('Taxes, Waiting for the GPU, #2 in line, 3 to send, unread');
    });

    it('says not sent, in the failed tone, when any queued message failed', () => {
        const text = rowText({ ...base, unsent: 3, failed: 1 }, t);
        expect(text).toMatchObject({ note: '1 not sent', noteFailed: true, aria: 'Taxes, 1 not sent' });
    });

    it('has no status or note on a quiet row', () => {
        expect(rowText(base, t)).toMatchObject({ status: null, note: null, aria: 'Taxes' });
    });
});

describe('chatListState', () => {
    it('is empty only once loaded with no visible chats, and error wins over pending', () => {
        expect(chatListState({ isPending: true, isError: false }, [])).toBe('loading');
        expect(chatListState({ isPending: false, isError: true }, [])).toBe('error');
        expect(chatListState({ isPending: true, isError: true }, [])).toBe('error');
        expect(chatListState({ isPending: false, isError: false }, [])).toBe('empty');
        expect(chatListState({ isPending: false, isError: false }, buildChatSections({ sessions: [session('a')], now: NOW }))).toBe('list');
    });
});

describe('day rollover', () => {
    it('moves a chat from Today to Yesterday once the next local midnight passes', () => {
        const evening = new Date(2026, 10, 12, 23, 59, 0);
        const chat = session('a', { updated_at: new Date(2026, 10, 12, 20, 0, 0).toISOString() });
        const titles = (now: Date) => buildChatSections({ sessions: [chat], now }).map((s) => s.key);
        expect(titles(evening)).toEqual(['Today']);
        const wait = msUntilNextMidnight(evening);
        expect(wait).toBe(60_000);
        expect(titles(new Date(evening.getTime() + wait))).toEqual(['Yesterday']);
    });
});
