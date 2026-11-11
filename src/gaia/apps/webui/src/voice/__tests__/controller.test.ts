// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { VoiceStatus } from '../../services/voiceApi';
import { useVoiceStore } from '../../stores/voiceStore';
import type { StreamEvent } from '../../types';
import type { ServerEvent } from '../protocol';
import type { VoiceTurnTap } from '../turnTap';

const fx = vi.hoisted(() => {
    const calls: string[] = [];
    const sockets: Array<Record<string, any>> = [];
    const players: Array<Record<string, any>> = [];
    const mics: Array<Record<string, any>> = [];
    return { calls, sockets, players, mics, micError: null as unknown };
});

vi.mock('../socket', () => ({
    VoiceSocket: vi.fn(function (this: Record<string, unknown>, purpose: string, language: string, handlers: unknown) {
        const s = {
            purpose, language, handlers,
            speak: vi.fn(), speakEnd: vi.fn(), commit: vi.fn(), close: vi.fn(), sendAudio: vi.fn(),
            interrupt: vi.fn(() => fx.calls.push('interrupt')),
        };
        fx.sockets.push(s);
        return s;
    }),
}));

vi.mock('../audio', async (orig) => ({
    ...(await orig<typeof import('../audio')>()),
    micSupported: () => true,
    createVoiceContext: () => ({ resume: vi.fn(async () => undefined), close: vi.fn(async () => undefined) }),
    MicCapture: vi.fn(function () {
        const m = {
            start: vi.fn(async () => { if (fx.micError) throw fx.micError; }),
            pause: vi.fn(), resume: vi.fn(), stop: vi.fn(), level: () => 0,
        };
        fx.mics.push(m);
        return m;
    }),
    SpeechPlayer: vi.fn(function () {
        const p = {
            pushed: 0, played: 0, playing: false, onIdle: () => {},
            push: vi.fn(), end: vi.fn(), close: vi.fn(), level: () => 0,
            flush: vi.fn(() => fx.calls.push('flush')),
            duck: vi.fn((on: boolean) => fx.calls.push(on ? 'duck' : 'unduck')),
        };
        fx.players.push(p);
        return p;
    }),
}));

const READY: VoiceStatus = {
    ready: true, error: null, error_code: null, language: 'en', asr_model: 'Moonshine-Tiny-Streaming', tts_model: 'kokoro-v1',
    speaks: true, models: [], turn_taking: 'hands_free', silence_ms: 500, status_updates: true, captions: true,
};
const status = vi.hoisted(() => ({ value: null as unknown }));
vi.mock('../../services/voiceApi', () => ({
    getVoiceStatus: vi.fn(async () => status.value),
    downloadVoiceModel: vi.fn(async (model: string) => ({ model, downloaded: true })),
}));

import { closeVoice, openVoice, primaryAction, type VoiceHost } from '../controller';

const state = () => useVoiceStore.getState();
const socket = () => fx.sockets[fx.sockets.length - 1];
const player = () => fx.players[fx.players.length - 1];
const serve = (event: ServerEvent) => (socket().handlers as { onEvent: (e: ServerEvent) => void }).onEvent(event);
const flushAsync = () => new Promise((r) => setTimeout(r, 0));

let tap: VoiceTurnTap | null;
function host(overrides: Partial<VoiceHost> = {}): VoiceHost & { sendTurn: ReturnType<typeof vi.fn>; stopTurn: ReturnType<typeof vi.fn> } {
    return {
        sendTurn: vi.fn((_text: string, t: VoiceTurnTap) => { tap = t; return true; }),
        stopTurn: vi.fn(),
        ...overrides,
    } as never;
}

async function open(h = host(), s: VoiceStatus = READY) {
    status.value = s;
    openVoice('s1', h);
    await flushAsync();
    await flushAsync();
    return h;
}

const chunk = (content: string) => ({ type: 'chunk', content }) as StreamEvent;

beforeEach(() => {
    fx.calls.length = 0;
    fx.sockets.length = 0;
    fx.players.length = 0;
    fx.mics.length = 0;
    fx.micError = null;
    tap = null;
});

afterEach(() => closeVoice());

describe('voice mode turn loop', () => {
    it('warms up, then listens once the socket is ready', async () => {
        status.value = READY;
        openVoice('s1', host());
        expect(state()).toMatchObject({ open: true, sessionId: 's1', phase: 'warming' });
        await flushAsync();
        await flushAsync();
        expect(socket().purpose).toBe('voice');
        expect(socket().language).toBe('en');
        serve({ type: 'ready', speech: true });
        expect(state().phase).toBe('listening');
    });

    it('sends what was said as a chat message, and speaks the spoken block as it streams', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'speech_started' });
        serve({ type: 'partial', text: 'find last week' });
        expect(state()).toMatchObject({ heard: 'find last week', heardPartial: true });
        serve({ type: 'speech_stopped' });
        expect(state().phase).toBe('thinking');
        serve({ type: 'final', text: "find last week's invoices" });
        expect(h.sendTurn).toHaveBeenCalledWith("find last week's invoices", expect.anything());
        expect(state().log[0]).toMatchObject({ kind: 'user', text: "find last week's invoices" });

        for (const c of '<spoken>Four invoices.</spoken> Detail') tap!.chunk(chunk(c));
        const said = socket().speak.mock.calls.filter(([, kind]: string[]) => kind === 'spoken').map(([text]: string[]) => text).join('');
        expect(said).toBe('Four invoices.');
        expect(socket().speakEnd).toHaveBeenCalledOnce();

        serve({ type: 'speaking', kind: 'spoken' });
        expect(state().phase).toBe('speaking');
        tap!.done({ type: 'done' } as StreamEvent);
        serve({ type: 'speaking_done' });
        expect(player().end).toHaveBeenCalled();
        expect(state().phase).toBe('listening');
        expect(state().turnRunning).toBe(false);
    });

    it('a sound over GAIA only lowers her voice; typing never cuts her off', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        serve({ type: 'speaking', kind: 'spoken' });
        serve({ type: 'speech_started' });
        serve({ type: 'duck', on: true });
        serve({ type: 'speech_stopped' });
        serve({ type: 'ignored', reason: 'not_voiced' });
        serve({ type: 'duck', on: false });
        expect(fx.calls).toEqual(['duck', 'unduck']);
        expect(state().phase).toBe('speaking');
        expect(state().log.filter((r) => r.kind === 'user')).toHaveLength(1);
    });

    it('a noise while nobody is talking sends nothing and goes back to listening', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'speech_started' });
        serve({ type: 'speech_stopped' });
        expect(state().phase).toBe('thinking');
        serve({ type: 'ignored', reason: 'not_words' });
        expect(state().phase).toBe('listening');
        expect(h.sendTurn).not.toHaveBeenCalled();
    });

    it('a trailing half-sentence keeps listening instead of sending', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'speech_started' });
        serve({ type: 'speech_stopped' });
        serve({ type: 'holding', text: 'find the invoices and' });
        expect(state()).toMatchObject({ phase: 'listening', heard: 'find the invoices and' });
        expect(h.sendTurn).not.toHaveBeenCalled();
        serve({ type: 'final', text: 'find the invoices and total them' });
        expect(h.sendTurn).toHaveBeenCalledWith('find the invoices and total them', expect.anything());
    });

    it('real speech over GAIA stops her when the server says so', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        serve({ type: 'speaking', kind: 'spoken' });
        serve({ type: 'speech_started' });
        serve({ type: 'barge_in' });
        expect(fx.calls).toEqual(['flush']);
        expect(state().phase).toBe('listening');
        // The agent keeps working; only Stop cancels it.
        expect(state().turnRunning).toBe(true);
    });

    it('speaks the fixed line when the answer has no spoken block', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        tap!.chunk(chunk('Just text.'));
        tap!.done({ type: 'done' } as StreamEvent);
        expect(socket().speak).toHaveBeenCalledWith('Done. The details are on screen.', 'spoken');
        expect(socket().speakEnd).toHaveBeenCalled();
    });

    it('speaks status lines only when that setting is on', async () => {
        await open(host(), { ...READY, status_updates: false });
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        tap!.agentEvent({ type: 'commentary', content: 'Looking in Downloads.' } as StreamEvent);
        expect(socket().speak).not.toHaveBeenCalledWith('Looking in Downloads.', 'status');
        expect(state().log[state().log.length - 1]).toMatchObject({ kind: 'status', text: 'Looking in Downloads.' });
        closeVoice();

        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        tap!.agentEvent({ type: 'commentary', content: 'Looking in Downloads.' } as StreamEvent);
        expect(socket().speak).toHaveBeenCalledWith('Looking in Downloads.', 'status');
    });

    it('in Arabic, shows the spoken summary as a caption and speaks nothing', async () => {
        await open(host(), { ...READY, language: 'ar', speaks: false, captions: false });
        serve({ type: 'ready', speech: false });
        serve({ type: 'final', text: 'ابحث عن الفواتير' });
        tap!.chunk(chunk('<spoken>وجدت أربع فواتير.</spoken> التفاصيل'));
        tap!.done({ type: 'done' } as StreamEvent);
        expect(socket().speak).not.toHaveBeenCalled();
        expect(state().captions).toBe(true);
        expect(state().caption).toEqual({ kind: 'spoken', text: 'وجدت أربع فواتير.', lit: Infinity });
    });

    it('holds what was said while a typed message is still running, and sends it after', async () => {
        const h = await open(host({ sendTurn: vi.fn(() => false) }));
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'and the total?' });
        expect(state().turnRunning).toBe(false);
        h.sendTurn = vi.fn(() => true);
        const { chatIdle } = await import('../controller');
        chatIdle();
        expect(h.sendTurn).toHaveBeenCalledWith('and the total?', expect.anything());
    });

    it('says out loud when a permission card needs an answer', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'clean up my downloads' });
        tap!.agentEvent({ type: 'permission_request', tool: 'run_shell_command' } as StreamEvent);
        expect(socket().speak).toHaveBeenCalledWith("I need your OK to go on. It's on screen.", 'status');
        expect(state().log[state().log.length - 1]).toMatchObject({ kind: 'note' });
    });

    it('a turn still streaming after voice mode closes writes nothing to the next session', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        const oldTap = tap!;
        const oldSocket = socket();
        closeVoice();
        await open();
        oldTap.agentEvent({ type: 'commentary', content: 'Looking.' } as StreamEvent);
        oldTap.chunk(chunk('<spoken>Done.</spoken>'));
        oldTap.done({ type: 'done' } as StreamEvent);
        expect(state().log).toEqual([]);
        expect(state().turnRunning).toBe(false);
        expect(oldSocket.speak).not.toHaveBeenCalled();
    });

    it('new words while GAIA is thinking stop that turn and are sent next', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'check my calendar' });
        expect(h.sendTurn).toHaveBeenCalledTimes(1);
        serve({ type: 'final', text: 'actually, check my email' });
        expect(h.stopTurn).toHaveBeenCalledOnce();
        // The stopped turn ends; what was said goes next, with no "stopped" notice.
        tap!.done({ type: 'done', cancelled: true } as StreamEvent);
        expect(h.sendTurn).toHaveBeenCalledTimes(2);
        expect(h.sendTurn.mock.calls[1][0]).toBe('actually, check my email');
        expect(state().log.some((r) => r.kind === 'note')).toBe(false);
    });

    it('"mm-hm" while GAIA is thinking changes nothing', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'check my calendar' });
        serve({ type: 'final', text: 'Mm-hmm.' });
        expect(h.stopTurn).not.toHaveBeenCalled();
        expect(state().log.filter((r) => r.kind === 'user')).toHaveLength(1);
    });

    it('the big button stops a running turn the normal way', async () => {
        const h = await open();
        serve({ type: 'ready', speech: true });
        serve({ type: 'final', text: 'hello' });
        expect(state().phase).toBe('thinking');
        primaryAction();
        expect(h.stopTurn).toHaveBeenCalledOnce();
    });

    it('the big button pauses and resumes listening', async () => {
        await open();
        serve({ type: 'ready', speech: true });
        primaryAction();
        expect(state().phase).toBe('paused');
        expect(fx.mics[fx.mics.length - 1].pause).toHaveBeenCalled();
        primaryAction();
        expect(state().phase).toBe('listening');
        expect(fx.mics[fx.mics.length - 1].resume).toHaveBeenCalled();
    });
});

describe('voice mode failures', () => {
    it('a missing speech model is model_missing, naming it, with no socket opened', async () => {
        await open(host(), {
            ...READY, ready: false, error_code: 'model_missing', error: 'Voice needs speech models: kokoro-v1',
            models: [{ id: 'Moonshine-Tiny-Streaming', purpose: 'transcription', downloaded: true, size_gb: 0.1 }, { id: 'kokoro-v1', purpose: 'tts', downloaded: false, size_gb: 0.3 }],
        });
        expect(state().error).toEqual({ code: 'model_missing', detail: 'kokoro-v1' });
        expect(fx.sockets).toHaveLength(0);
    });

    it('a refused microphone is mic_denied', async () => {
        fx.micError = new DOMException('Permission denied', 'NotAllowedError');
        await open();
        expect(state().error?.code).toBe('mic_denied');
    });

    it('a microphone held by another app is mic_busy', async () => {
        fx.micError = new DOMException('Could not start audio source', 'NotReadableError');
        await open();
        expect(state().error?.code).toBe('mic_busy');
    });

    it('the server unreachable is lemonade_unreachable', async () => {
        await open(host(), { ...READY, ready: false, error_code: 'lemonade_unreachable', error: 'Lemonade not reachable' });
        expect(state().error).toEqual({ code: 'lemonade_unreachable', detail: 'Lemonade not reachable' });
    });

    it('a dropped socket stops the mic and shows session_lost', async () => {
        await open();
        (socket().handlers as { onFailure: (f: unknown) => void }).onFailure({ code: 'session_lost', detail: 'closed with code 1006' });
        expect(state().error?.code).toBe('session_lost');
        expect(fx.mics[fx.mics.length - 1].stop).toHaveBeenCalled();
    });
});
