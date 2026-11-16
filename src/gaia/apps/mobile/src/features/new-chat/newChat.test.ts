// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SystemStatus, WarmUpStatus } from '@webui/types';

import { createFakeClient } from '@/api/fake';
import { translator } from '@/i18n/useT';

import { GaiaApiError } from '@/api/errors';
import { IDLE_TURN, type ChatTurn } from '@/state/chatStreams';

import { DOCS_SHOWN, foldDocuments, splitDocuments } from './attachedDocs';
import { setupError } from './setupFailed';
import { withExample } from './tryExamples';
import { createFirstSend } from './firstSend';
import { greetingKey } from './greeting';
import { startingReason } from './starting';
import { assistantProblem } from './useNewChat';
import { warmUpLine, warmUpProgress } from './warmUp';

const t = translator('en');

const turn = (patch: Partial<ChatTurn>): ChatTurn => ({ ...IDLE_TURN, ...patch });

function setup(overrides: { createSession?: () => Promise<{ id: string }>; deliver?: (id: string, text: string) => Promise<boolean | void> } = {}) {
    const fake = createFakeClient();
    const queued: { id: string; items: { text: string; state: string; error?: string }[] }[] = [];
    const held: string[] = [];
    const flushed: string[] = [];
    const flow = createFirstSend({
        createSession: overrides.createSession ?? (async () => ({ id: 's1' })),
        deliver:
            overrides.deliver ??
            (async (id, text) => {
                for await (const event of fake.sendMessage(id, text)) void event;
            }),
        takeHeld: () => held.splice(0),
        flush: async (id) => void flushed.push(id),
        queueHeld: (id, items) => queued.push({ id, items }),
        describeError: (err) => (err instanceof Error ? err.message : String(err)),
        firstNotSent: 'The first message was not sent.',
    });
    const submit = (text: string) => flow.submit(text, (m) => held.push(m));
    return { fake, queued, flushed, held, flow, submit };
}

describe('first send', () => {
    it('creates the chat, sends the first message, and opens the chat once its turn is under way', async () => {
        const { fake, flushed, flow, submit } = setup();
        await submit('hello');
        expect(fake.sent).toEqual([{ sessionId: 's1', message: 'hello' }]);
        expect(flow.getState()).toMatchObject({ text: '', sending: true, opening: 'hello', waiting: 's1', open: null });
        flow.observe(turn({ status: 'connecting' }));
        expect(flow.getState().open).toBeNull();
        flow.observe(turn({ status: 'streaming' }));
        expect(flow.getState()).toMatchObject({ sending: false, open: 's1', waiting: null });
        expect(flushed).toEqual(['s1']);
    });

    it('ignores an empty message', async () => {
        const { fake, submit } = setup();
        await submit('');
        expect(fake.sent).toEqual([]);
    });

    it('holds messages typed while the chat is created', async () => {
        let release!: () => void;
        const gate = new Promise<void>((r) => (release = r));
        const { held, flow, submit } = setup({ createSession: async () => (await gate, { id: 's1' }) });
        const first = submit('first');
        await submit('second');
        expect(held).toEqual(['second']);
        expect(flow.getState().text).toBe('');
        release();
        await first;
        expect(held).toEqual(['second']);
    });

    it('shows held messages as failed when the first one could not start', async () => {
        const { queued, held, flow, submit } = setup({ deliver: async () => false });
        held.push('second');
        await submit('first');
        expect(queued[0].items).toMatchObject([{ text: 'second', state: 'failed', error: 'The first message was not sent.' }]);
        expect(flow.getState()).toMatchObject({ sending: false, open: 's1' });
    });

    it('puts everything back in the box, joined by a blank line, when the chat cannot be created', async () => {
        const { held, flow, submit } = setup({ createSession: async () => Promise.reject(new Error('Lemonade is down')) });
        held.push('second');
        await submit('first');
        expect(flow.getState()).toMatchObject({ text: 'first\n\nsecond', sending: false, error: 'Lemonade is down', open: null });
        expect(held).toEqual([]);
    });

    it('puts everything back when the first turn fails before it starts', async () => {
        const { held, flushed, flow, submit } = setup();
        held.push('second');
        await submit('first');
        flow.observe(turn({ status: 'idle', error: new GaiaApiError(500, 'boom') }));
        expect(flow.getState()).toMatchObject({ text: 'first\n\nsecond', sending: false, open: null, waiting: null });
        expect(flow.getState().error).toContain('mobileApp.error.http');
        expect(flushed).toEqual([]);
    });
});

describe('greeting', () => {
    it.each([
        [4, 'evening'],
        [5, 'morning'],
        [11, 'morning'],
        [12, 'afternoon'],
        [17, 'afternoon'],
        [18, 'evening'],
        [0, 'evening'],
    ])('at hour %i reads as %s', (hour, part) => {
        const now = new Date(2026, 10, 12, hour, 30);
        expect(greetingKey(now)).toBe(`newChat.greeting.${part}`);
        expect(t(greetingKey(now))).toContain(part === 'morning' ? 'morning' : part);
    });
});

describe('warm-up line', () => {
    const warming = (patch: Partial<WarmUpStatus>): WarmUpStatus => ({ state: 'warming', elapsed_seconds: 7.9, ...patch });
    const name = (m: string) => m;

    it('is hidden unless warming', () => {
        expect(warmUpProgress({ state: 'ready' })).toBeNull();
        expect(warmUpProgress(undefined)).toBeNull();
    });

    it('says which model and where once known', () => {
        expect(warmUpLine(warmUpProgress(warming({}))!, t, name)).toBe('Getting GAIA ready… 7s');
        expect(warmUpLine(warmUpProgress(warming({ model: 'Gemma' }))!, t, name)).toBe('Loading Gemma… 7s');
        expect(warmUpLine(warmUpProgress(warming({ model: 'Gemma', device: 'npu' }))!, t, name)).toBe(
            'Loading Gemma on your NPU… 7s',
        );
    });
});

describe('starting lock', () => {
    const status = (patch: Partial<SystemStatus>) => patch as SystemStatus;

    it('disables the composer only while boot tasks are pending', () => {
        expect(startingReason(status({ init_state: 'initializing', init_tasks: [{ name: 'a', status: 'running' }] }), t)).toBe(
            'GAIA is starting…',
        );
        expect(startingReason(status({ init_state: 'initializing', init_tasks: [{ name: 'a', status: 'done' }] }), t)).toBeNull();
        expect(startingReason(status({ init_state: 'ready' }), t)).toBeNull();
        expect(startingReason(undefined, t)).toBeNull();
    });
});

describe('assistant did not start', () => {
    const screen = { cards: [], working_folder: { path: '/', name: '/', gaia_folder: false, choosable: false } };

    it('reads a failed request first, then the reason the agent reported', () => {
        expect(assistantProblem(new Error('boom'), undefined, t)).toBe('boom');
        expect(assistantProblem(null, { ...screen, skills_error: 'no skills' }, t)).toBe('no skills');
        expect(assistantProblem(null, screen, t)).toBeNull();
        expect(assistantProblem(null, undefined, t)).toBeNull();
    });
});

describe('try-an-example chips', () => {
    it('fill an empty box, add after typed text, and swap the last example for the next', () => {
        const first = withExample('', '', 'Plan 3 days in Lisbon');
        expect(first).toBe('Plan 3 days in Lisbon');
        expect(withExample('Something for two', '', 'Plan 3 days in Lisbon')).toBe('Something for two\n\nPlan 3 days in Lisbon');
        expect(withExample(first, 'Plan 3 days in Lisbon', 'A 15-minute workout')).toBe('A 15-minute workout');
        expect(withExample('Mine\n\nPlan 3 days in Lisbon', 'Plan 3 days in Lisbon', 'A 15-minute workout')).toBe(
            'Mine\n\nA 15-minute workout',
        );
    });
});

describe('documents for the new chat', () => {
    const doc = (id: string, status = 'complete') =>
        ({ id, filename: `${id}.txt`, indexing_status: status }) as Parameters<typeof splitDocuments>[0][number];

    it('splits attached from offered, offering only indexed documents', () => {
        const { attached, offered } = splitDocuments([doc('a'), doc('b'), doc('c', 'indexing')], ['a']);
        expect(attached.map((d) => d.id)).toEqual(['a']);
        expect(offered.map((d) => d.id)).toEqual(['b']);
    });

    it('folds past five until expanded', () => {
        const ids = Array.from({ length: DOCS_SHOWN + 3 }, (_, i) => i);
        expect(foldDocuments(ids, false)).toEqual({ shown: ids.slice(0, DOCS_SHOWN), hidden: 3 });
        expect(foldDocuments(ids, true)).toEqual({ shown: ids, hidden: 3 });
        expect(foldDocuments(ids.slice(0, DOCS_SHOWN), false).hidden).toBe(0);
    });
});

describe('setup-failed banner', () => {
    const status = (patch: Partial<SystemStatus>) => ({ ...patch }) as SystemStatus;

    it('shows the error until a model has loaded', () => {
        expect(setupError(undefined)).toBeNull();
        expect(setupError(status({ setup_error: 'disk full', model_loaded: null }))).toBe('disk full');
        expect(setupError(status({ setup_error: 'disk full', model_loaded: 'Gemma' }))).toBeNull();
        expect(setupError(status({ model_loaded: null }))).toBeNull();
    });
});
