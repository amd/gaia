// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Reads one reply aloud at a time through Lemonade's Kokoro voice.
 *
 * The reply is spoken in pieces: the next piece is fetched while the current
 * one plays, so a long reply starts within a second or two. Starting another
 * reply, or stopping, cancels the one playing.
 */

import { create } from 'zustand';
import * as api from '../services/api';
import * as speechApi from '../services/speechApi';
import { log } from '../utils/logger';
import { speechPieces } from '../utils/speechText';

/** `downloading`: Lemonade is fetching the voice model for this first read. */
export type ReadAloudStatus = 'loading' | 'downloading' | 'playing';

/** What went wrong, for a translated message: Lemonade unreachable (503),
 *  Lemonade failed to speak (502), the browser could not play it, the reply
 *  has nothing to say (only code), or other. */
export type ReadAloudErrorKind = 'unreachable' | 'speech' | 'playback' | 'nothing' | 'other';

export interface ReadAloudError {
    messageId: string;
    kind: ReadAloudErrorKind;
    /** The server's or browser's own words, shown under the translated line. */
    detail: string;
}

class PlaybackError extends Error {}

function describe(messageId: string, err: unknown): ReadAloudError {
    if (err instanceof api.ApiError) {
        const kind = err.httpStatus === 503 ? 'unreachable' : err.httpStatus === 502 ? 'speech' : 'other';
        return { messageId, kind, detail: err.detail || err.message };
    }
    if (err instanceof PlaybackError || (err instanceof DOMException && err.name === 'NotAllowedError')) {
        return { messageId, kind: 'playback', detail: err.message };
    }
    return { messageId, kind: 'other', detail: err instanceof Error ? err.message : String(err) };
}

interface ReadAloudState {
    /** The message being read, or null. */
    messageId: string | null;
    status: ReadAloudStatus | null;
    /** Why the last reading stopped early, for the message it was reading. */
    error: ReadAloudError | null;
    /** The saved voice settings, once read from the server. */
    settings: speechApi.SpeechSettings | null;
    /** The voice model is known to be on this PC, so reads skip the check. */
    voiceReady: boolean;
    /** Size of the voice model being downloaded for the current read, in GB. */
    downloadGb: number | null;
    setSettings: (settings: speechApi.SpeechSettings) => void;
    read: (messageId: string, markdown: string) => Promise<void>;
    stop: () => void;
    clearError: () => void;
}

/** One reading in progress; replaced, never reused, when another starts. */
interface Run {
    abort: AbortController;
    audio: HTMLAudioElement | null;
    urls: string[];
}

let run: Run | null = null;

function endRun(r: Run): void {
    r.abort.abort();
    if (r.audio) {
        r.audio.pause();
        r.audio.src = '';
    }
    r.urls.forEach((u) => URL.revokeObjectURL(u));
    r.urls = [];
}

/** Play *blob* to the end; resolves early, without error, when *r* is cancelled. */
function play(r: Run, blob: Blob): Promise<void> {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(blob);
        r.urls.push(url);
        const audio = new Audio(url);
        r.audio = audio;
        const done = () => {
            r.abort.signal.removeEventListener('abort', done);
            resolve();
        };
        audio.onended = done;
        audio.onerror = () => reject(new PlaybackError(audio.error?.message || `media error ${audio.error?.code ?? ''}`.trim()));
        r.abort.signal.addEventListener('abort', done);
        audio.play().catch(reject);
    });
}

export const useReadAloudStore = create<ReadAloudState>((set, get) => ({
    messageId: null,
    status: null,
    error: null,
    settings: null,
    voiceReady: false,
    downloadGb: null,
    setSettings: (settings) => set({ settings }),

    stop: () => {
        if (run) endRun(run);
        run = null;
        set({ messageId: null, status: null, downloadGb: null });
    },

    clearError: () => set({ error: null }),

    read: async (messageId, markdown) => {
        get().stop();
        const pieces = speechPieces(markdown, speechApi.MAX_SPEECH_CHARS);
        if (pieces.length === 0) {
            set({ error: { messageId, kind: 'nothing', detail: '' } });
            return;
        }
        const mine: Run = { abort: new AbortController(), audio: null, urls: [] };
        run = mine;
        set({ messageId, status: 'loading', error: null });
        const fetchPiece = (text: string) => {
            const pending = speechApi.speak(text, { signal: mine.abort.signal });
            // Awaited in turn below; this only keeps a piece fetched ahead of a
            // stop from being reported as an unhandled rejection.
            pending.catch(() => undefined);
            return pending;
        };
        try {
            if (!get().voiceReady) {
                // The first speech request pulls a missing model; say so instead of a bare spinner.
                const model = await speechApi.getSpeechModel();
                if (run !== mine) return;
                if (!model.downloaded) set({ status: 'downloading', downloadGb: model.size_gb });
            }
            let next = fetchPiece(pieces[0]);
            for (let i = 0; i < pieces.length; i++) {
                const blob = await next;
                if (run !== mine) return;
                if (i + 1 < pieces.length) next = fetchPiece(pieces[i + 1]);
                set({ status: 'playing', voiceReady: true, downloadGb: null });
                await play(mine, blob);
                if (run !== mine) return;
            }
        } catch (err) {
            if (run !== mine || mine.abort.signal.aborted) return;
            log.ui.error('Read aloud failed', err);
            set({ error: describe(messageId, err) });
        }
        if (run === mine) {
            endRun(mine);
            run = null;
            set({ messageId: null, status: null, downloadGb: null });
        }
    },
}));
