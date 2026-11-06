// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * One voice session over `WS /api/voice/session`: microphone PCM up, events and
 * speech PCM down. Same origin as the API, so mobile access sends its cookie.
 */

import { getApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';
import { isVoiceErrorCode, type ClientMessage, type ServerEvent, type SpeakKind, type VoiceFailure, type VoicePurpose } from './protocol';

export interface VoiceSocketHandlers {
    onEvent: (event: ServerEvent) => void;
    /** PCM16 mono 24 kHz speech. */
    onAudio: (pcm: ArrayBuffer) => void;
    /** The session can't go on; called once. */
    onFailure: (failure: VoiceFailure) => void;
}

type SocketCtor = new (url: string) => WebSocket;

/** `ws(s)://…/api/voice/session` for whichever host serves the API. */
export function voiceSocketUrl(apiBase: string = getApiBase(), here: string = window.location.href): string {
    const url = new URL(`${apiBase}/voice/session`, here);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.href;
}

export class VoiceSocket {
    private ws: WebSocket;
    private ended = false;
    private failed = false;

    constructor(
        purpose: VoicePurpose,
        language: string,
        private handlers: VoiceSocketHandlers,
        { url = voiceSocketUrl(), Socket = WebSocket as SocketCtor }: { url?: string; Socket?: SocketCtor } = {},
    ) {
        this.ws = new Socket(url);
        this.ws.binaryType = 'arraybuffer';
        this.ws.onopen = () => this.send({ type: 'start', purpose, language });
        this.ws.onmessage = (e: MessageEvent) => this.receive(e.data);
        this.ws.onerror = () => log.ui.warn('Voice socket error');
        this.ws.onclose = (e: CloseEvent) => {
            if (this.ended || this.failed) return;
            log.ui.warn(`Voice socket closed unexpectedly (code ${e.code}${e.reason ? `: ${e.reason}` : ''})`);
            this.fail({ code: 'session_lost', detail: e.reason || `closed with code ${e.code}` });
        };
    }

    get isOpen(): boolean {
        return this.ws.readyState === WebSocket.OPEN && !this.ended && !this.failed;
    }

    /** One PCM16 16 kHz mono frame. Dropped until the socket is open. */
    sendAudio(frame: ArrayBuffer): void {
        if (this.isOpen) this.ws.send(frame);
    }

    speak(text: string, kind: SpeakKind): void {
        if (text) this.send({ type: 'speak', text, kind });
    }

    speakEnd(): void { this.send({ type: 'speak_end' }); }
    interrupt(): void { this.send({ type: 'interrupt' }); }
    commit(): void { this.send({ type: 'commit' }); }

    /** End the session from this side. */
    close(): void {
        if (this.ended) return;
        this.send({ type: 'stop' });
        this.ended = true;
        this.ws.close();
    }

    private send(message: ClientMessage): void {
        if (this.ws.readyState !== WebSocket.OPEN || this.ended || this.failed) return;
        this.ws.send(JSON.stringify(message));
    }

    private fail(failure: VoiceFailure): void {
        if (this.failed || this.ended) return;
        this.failed = true;
        this.handlers.onFailure(failure);
        if (this.ws.readyState === WebSocket.OPEN) this.ws.close();
    }

    private receive(data: unknown): void {
        if (data instanceof ArrayBuffer) {
            this.handlers.onAudio(data);
            return;
        }
        let event: ServerEvent;
        try {
            event = JSON.parse(String(data)) as ServerEvent;
        } catch (err) {
            log.ui.error('Voice socket sent a message that is not JSON', err);
            this.fail({ code: 'bad_request', detail: String(data).slice(0, 120) });
            return;
        }
        if (event.type === 'error') {
            log.ui.warn(`Voice error ${event.code}: ${event.message}`, event.detail ?? '');
            // A reply that couldn't be spoken leaves the session running.
            if (event.code === 'tts_failed') {
                this.handlers.onEvent(event);
                return;
            }
            this.fail({
                code: isVoiceErrorCode(event.code) ? event.code : 'bad_request',
                detail: event.detail || event.message,
            });
            return;
        }
        if (event.type === 'closed') {
            // The server closes after its own error; one that closes without saying why lost the session.
            if (!this.ended) this.fail({ code: 'session_lost', detail: 'the server ended the voice session' });
            return;
        }
        this.handlers.onEvent(event);
    }
}
