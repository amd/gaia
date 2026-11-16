// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import { MAX_SPEECH_CHARS, speechPieces } from '@webui/utils/speechText';

import type { GaiaClient, PcAudioBytes } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';

import { PcAudioPlaybackError, playPcAudio } from './playPcAudio';

/** `downloading`: the PC is fetching the voice model for this first read. */
export type ReadAloudStatus = 'loading' | 'downloading' | 'playing';

/** The web's kinds: the model server unreachable (503), it failed to speak (502), the phone couldn't play it, only code, or other. `unsupported`: the PC's GAIA predates the speech routes (404). */
export type ReadAloudErrorKind = 'unsupported' | 'unreachable' | 'speech' | 'playback' | 'nothing' | 'other';

export interface ReadAloudError {
    messageId: string;
    kind: ReadAloudErrorKind;
    /** What failed, shown under the translated line; null for `nothing`. */
    cause: unknown;
}

interface ReadAloudState {
    /** The reply (`<session>:<message>`) or preview being read, or null. */
    messageId: string | null;
    status: ReadAloudStatus | null;
    error: ReadAloudError | null;
    /** The voice model is known to be on the PC, so reads skip the check. */
    voiceReady: boolean;
    /** Size of the voice model downloading for the current read, in GB. */
    downloadGb: number | null;
    read: (client: GaiaClient, messageId: string, markdown: string) => Promise<void>;
    stop: () => void;
    clearError: () => void;
}

export function kindOf(err: unknown): ReadAloudErrorKind {
    if (err instanceof GaiaApiError) {
        if (err.status === 404) return 'unsupported';
        return err.status === 503 ? 'unreachable' : err.status === 502 ? 'speech' : 'other';
    }
    return err instanceof PcAudioPlaybackError ? 'playback' : 'other';
}

/** One reading; replaced, never reused, when another starts. */
let run: AbortController | null = null;

/** Reads one reply aloud at a time: the next piece is fetched while the current one plays. */
export const useReadAloudStore = create<ReadAloudState>((set, get) => ({
    messageId: null,
    status: null,
    error: null,
    voiceReady: false,
    downloadGb: null,

    stop: () => {
        run?.abort();
        run = null;
        set({ messageId: null, status: null, downloadGb: null });
    },

    clearError: () => set({ error: null }),

    read: async (client, messageId, markdown) => {
        get().stop();
        const pieces = speechPieces(markdown, MAX_SPEECH_CHARS);
        if (pieces.length === 0) {
            set({ error: { messageId, kind: 'nothing', cause: null } });
            return;
        }
        const mine = new AbortController();
        run = mine;
        set({ messageId, status: 'loading', error: null });
        const fetchPiece = (text: string) => {
            const pending = client.speak(text, undefined, { signal: mine.signal });
            // Awaited in turn below; this keeps a piece fetched ahead of a stop from going unhandled.
            pending.catch(() => undefined);
            return pending;
        };
        try {
            if (!get().voiceReady) {
                // The first speech request pulls a missing model; say so instead of a bare spinner.
                const model = await client.getSpeechModel({ signal: mine.signal });
                if (run !== mine) return;
                if (!model.downloaded) set({ status: 'downloading', downloadGb: model.size_gb });
            }
            let next: Promise<PcAudioBytes> = fetchPiece(pieces[0]!);
            for (let i = 0; i < pieces.length; i++) {
                const audio = await next;
                if (run !== mine) return;
                if (i + 1 < pieces.length) next = fetchPiece(pieces[i + 1]!);
                set({ status: 'playing', voiceReady: true, downloadGb: null });
                await playPcAudio(audio, mine.signal);
                if (run !== mine) return;
            }
        } catch (err) {
            if (run !== mine || mine.signal.aborted) return;
            set({ error: { messageId, kind: kindOf(err), cause: err } });
        }
        if (run === mine) {
            run = null;
            set({ messageId: null, status: null, downloadGb: null });
        }
    },
}));
