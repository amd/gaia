// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceSocket, voiceSocketUrl, type VoiceSocketHandlers } from '../socket';

/** A WebSocket that records what was sent and lets the test play the server. */
class FakeSocket {
    static last: FakeSocket;
    readyState = 0;
    binaryType = 'blob';
    sent: Array<string | ArrayBuffer> = [];
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onclose: ((e: { code: number; reason: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    constructor(readonly url: string) { FakeSocket.last = this; }
    send(data: string | ArrayBuffer) { this.sent.push(data); }
    close() { this.closed = true; this.readyState = 3; }
    open() { this.readyState = 1; this.onopen?.(); }
    serve(event: object) { this.onmessage?.({ data: JSON.stringify(event) }); }
    drop(code = 1006, reason = '') { this.readyState = 3; this.onclose?.({ code, reason }); }
    json() { return this.sent.filter((m): m is string => typeof m === 'string').map((m) => JSON.parse(m)); }
}

function handlers(): VoiceSocketHandlers & { [K in keyof VoiceSocketHandlers]: ReturnType<typeof vi.fn> } {
    return { onEvent: vi.fn(), onAudio: vi.fn(), onFailure: vi.fn() } as never;
}

function connect(h = handlers(), purpose: 'voice' | 'dictation' = 'voice', language = 'en') {
    const socket = new VoiceSocket(purpose, language, h, { url: 'ws://x/api/voice/session', Socket: FakeSocket as never });
    const ws = FakeSocket.last;
    ws.open();
    return { socket, ws, h };
}

beforeEach(() => {
    vi.stubGlobal('WebSocket', Object.assign(FakeSocket, { OPEN: 1, CLOSED: 3 }));
});

describe('voiceSocketUrl', () => {
    it('follows the page: ws for http, wss for https (mobile access)', () => {
        expect(voiceSocketUrl('/api', 'http://localhost:5174/')).toBe('ws://localhost:5174/api/voice/session');
        expect(voiceSocketUrl('/api', 'https://abc.ngrok.app/')).toBe('wss://abc.ngrok.app/api/voice/session');
        expect(voiceSocketUrl('http://127.0.0.1:4321/api', 'file:///C:/app/index.html')).toBe('ws://127.0.0.1:4321/api/voice/session');
    });
});

describe('VoiceSocket', () => {
    it('opens with one start message naming the purpose and language', () => {
        const { ws } = connect(undefined, 'dictation', 'ar');
        expect(ws.binaryType).toBe('arraybuffer');
        expect(ws.json()).toEqual([{ type: 'start', purpose: 'dictation', language: 'ar' }]);
    });

    it('sends microphone frames as binary, untouched', () => {
        const { socket, ws } = connect();
        const frame = new Int16Array(1600).buffer;
        socket.sendAudio(frame);
        expect(ws.sent[1]).toBe(frame);
    });

    it('drops frames before the socket opens', () => {
        const h = handlers();
        const socket = new VoiceSocket('voice', 'en', h, { url: 'ws://x', Socket: FakeSocket as never });
        socket.sendAudio(new ArrayBuffer(8));
        expect(FakeSocket.last.sent).toEqual([]);
    });

    it('sends speak, speak_end, interrupt and commit in the protocol shape', () => {
        const { socket, ws } = connect();
        socket.speak('Found two invoices.', 'spoken');
        socket.speak('Looking in Downloads.', 'status');
        socket.speak('', 'spoken');
        socket.speakEnd();
        socket.interrupt();
        socket.commit();
        expect(ws.json().slice(1)).toEqual([
            { type: 'speak', text: 'Found two invoices.', kind: 'spoken' },
            { type: 'speak', text: 'Looking in Downloads.', kind: 'status' },
            { type: 'speak_end' },
            { type: 'interrupt' },
            { type: 'commit' },
        ]);
    });

    it('passes events and speech audio through', () => {
        const { ws, h } = connect();
        ws.serve({ type: 'ready', speech: true });
        ws.serve({ type: 'flush' });
        const pcm = new ArrayBuffer(4800);
        ws.onmessage?.({ data: pcm });
        expect(h.onEvent).toHaveBeenCalledWith({ type: 'ready', speech: true });
        expect(h.onEvent).toHaveBeenCalledWith({ type: 'flush' });
        expect(h.onAudio).toHaveBeenCalledWith(pcm);
    });

    it('reports a server error once, by its stable code, with the detail kept', () => {
        const { ws, h } = connect();
        ws.serve({ type: 'error', code: 'model_missing', message: 'Download Moonshine-Tiny-Streaming', detail: 'Moonshine-Tiny-Streaming' });
        ws.serve({ type: 'closed' });
        expect(h.onFailure).toHaveBeenCalledTimes(1);
        expect(h.onFailure).toHaveBeenCalledWith({ code: 'model_missing', detail: 'Moonshine-Tiny-Streaming' });
        expect(ws.closed).toBe(true);
    });

    it('keeps the session going when only speech failed', () => {
        const { ws, h } = connect();
        ws.serve({ type: 'error', code: 'tts_failed', message: "Couldn't speak this reply." });
        expect(h.onFailure).not.toHaveBeenCalled();
        expect(h.onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', code: 'tts_failed' }));
    });

    it('maps an unknown code to bad_request instead of showing raw text', () => {
        const { ws, h } = connect();
        ws.serve({ type: 'error', code: 'kaboom', message: 'Traceback (most recent call last)' });
        expect(h.onFailure).toHaveBeenCalledWith({ code: 'bad_request', detail: 'Traceback (most recent call last)' });
    });

    it('a dropped connection is session_lost', () => {
        const { ws, h } = connect();
        ws.drop(1006);
        expect(h.onFailure).toHaveBeenCalledWith({ code: 'session_lost', detail: 'closed with code 1006' });
    });

    it('a server that closes without an error is session_lost', () => {
        const { ws, h } = connect();
        ws.serve({ type: 'closed' });
        expect(h.onFailure).toHaveBeenCalledWith(expect.objectContaining({ code: 'session_lost' }));
    });

    it('closing from this side sends stop and reports nothing', () => {
        const { socket, ws, h } = connect();
        socket.close();
        ws.drop(1000);
        expect(ws.json().slice(-1)[0]).toEqual({ type: 'stop' });
        expect(h.onFailure).not.toHaveBeenCalled();
    });
});
