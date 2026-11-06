// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Dictation into the composer: a voice session that only listens.
 *
 * Pauses don't end it; each recognized segment is joined to the last. Stopping
 * commits the words still being recognized and hands the whole transcript back.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../services/api';
import { downloadVoiceModel, getVoiceStatus, type VoiceModel } from '../services/voiceApi';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';
import { createVoiceContext, MicCapture, micErrorCode, micSupported } from '../voice/audio';
import type { VoiceFailure } from '../voice/protocol';
import { VoiceSocket } from '../voice/socket';

export type DictationState = 'idle' | 'recording' | 'finishing';

/** How long to wait for the last words after Stop before using what was shown. */
const FINAL_WAIT_MS = 4000;

export interface Dictation {
    state: DictationState;
    /** Segments already recognized. */
    committed: string;
    /** The segment being recognized now. */
    partial: string;
    elapsed: number;
    error: VoiceFailure | null;
    /** For `model_missing`: the model to download, with its size. */
    missingModel: VoiceModel | null;
    downloading: boolean;
    downloadError: string | null;
    /** The five-minute cap stopped the recording. */
    limited: boolean;
    start: () => void;
    stop: () => void;
    dismissError: () => void;
    download: () => void;
    level: () => number;
}

interface Run {
    ctx: AudioContext;
    mic: MicCapture;
    socket: VoiceSocket;
    finals: string[];
    partial: string;
    startedAt: number;
    waiting: ReturnType<typeof setTimeout> | null;
}

export function useDictation(onText: (text: string) => void): Dictation {
    const [state, setState] = useState<DictationState>('idle');
    const [committed, setCommitted] = useState('');
    const [partial, setPartial] = useState('');
    const [elapsed, setElapsed] = useState(0);
    const [error, setError] = useState<VoiceFailure | null>(null);
    const [missingModel, setMissingModel] = useState<VoiceModel | null>(null);
    const [downloading, setDownloading] = useState(false);
    const [downloadError, setDownloadError] = useState<string | null>(null);
    const [limited, setLimited] = useState(false);
    const run = useRef<Run | null>(null);
    const onTextRef = useRef(onText);
    onTextRef.current = onText;

    const teardown = useCallback((r: Run) => {
        if (r.waiting) clearTimeout(r.waiting);
        r.socket.close();
        r.mic.stop();
        void r.ctx.close();
        if (run.current === r) run.current = null;
    }, []);

    const finish = useCallback((r: Run, timedOut: boolean) => {
        if (run.current !== r) return;
        if (timedOut && r.partial) log.ui.warn('Dictation: no final transcript after Stop; inserting the words shown');
        const text = [...r.finals, ...(timedOut && r.partial ? [r.partial] : [])].join(' ').replace(/\s+/g, ' ').trim();
        teardown(r);
        setState('idle');
        setCommitted('');
        setPartial('');
        if (text) onTextRef.current(text);
    }, [teardown]);

    const failRun = useCallback((r: Run, failure: VoiceFailure) => {
        if (run.current !== r) return;
        log.ui.warn(`Dictation stopped: ${failure.code}`, failure.detail ?? '');
        // Keep what was already recognized.
        const text = r.finals.join(' ').trim();
        teardown(r);
        setState('idle');
        setCommitted('');
        setPartial('');
        setError(failure);
        if (text) onTextRef.current(text);
        if (failure.code === 'model_missing') {
            getVoiceStatus(useChatStore.getState().locale)
                .then((s) => setMissingModel(s.models.find((m) => !m.downloaded && m.purpose === 'transcription') ?? s.models.find((m) => !m.downloaded) ?? null))
                .catch((err: unknown) => setDownloadError(err instanceof Error ? err.message : String(err)));
        }
    }, [teardown]);

    const stop = useCallback(() => {
        const r = run.current;
        if (!r) return;
        if (!r.partial) {
            finish(r, false);
            return;
        }
        setState('finishing');
        r.socket.commit();
        r.waiting = setTimeout(() => finish(r, true), FINAL_WAIT_MS);
    }, [finish]);

    const start = useCallback(() => {
        if (run.current) return;
        setError(null);
        setMissingModel(null);
        setDownloadError(null);
        setLimited(false);
        if (!micSupported()) {
            setError({ code: 'mic_missing', detail: 'this page has no microphone access (a secure context is required)' });
            return;
        }
        const ctx = createVoiceContext();
        let r: Run | null = null;
        const mic = new MicCapture(ctx, (frame) => r?.socket.sendAudio(frame));
        const socket = new VoiceSocket('dictation', useChatStore.getState().locale, {
            onEvent: (event) => {
                if (!r || run.current !== r) return;
                if (event.type === 'partial') {
                    r.partial = event.text;
                    setPartial(event.text);
                } else if (event.type === 'final') {
                    const text = event.text.trim();
                    if (text) r.finals.push(text);
                    r.partial = '';
                    setCommitted(r.finals.join(' '));
                    setPartial('');
                    if (r.waiting) finish(r, false);
                } else if (event.type === 'limit') {
                    setLimited(true);
                    stopRef.current();
                }
            },
            onAudio: () => {},
            onFailure: (failure) => { if (r) failRun(r, failure); },
        });
        r = { ctx, mic, socket, finals: [], partial: '', startedAt: Date.now(), waiting: null };
        run.current = r;
        setState('recording');
        setElapsed(0);
        const mine = r;
        mic.start().catch((err: unknown) => failRun(mine, { code: micErrorCode(err), detail: err instanceof Error ? err.message : String(err) }));
    }, [failRun, finish]);

    const stopRef = useRef(stop);
    stopRef.current = stop;

    useEffect(() => {
        if (state !== 'recording') return;
        const timer = setInterval(() => {
            if (run.current) setElapsed(Math.floor((Date.now() - run.current.startedAt) / 1000));
        }, 250);
        return () => clearInterval(timer);
    }, [state]);

    // Leaving the chat ends the recording.
    useEffect(() => () => { if (run.current) teardown(run.current); }, [teardown]);

    const download = useCallback(() => {
        if (!missingModel || downloading) return;
        setDownloading(true);
        setDownloadError(null);
        downloadVoiceModel(missingModel.id)
            .then(() => {
                setDownloading(false);
                setMissingModel(null);
                setError(null);
                start();
            })
            .catch((err: unknown) => {
                setDownloading(false);
                setDownloadError(err instanceof ApiError ? err.detail || err.message : String(err));
            });
    }, [missingModel, downloading, start]);

    return {
        state, committed, partial, elapsed, error, missingModel, downloading, downloadError, limited,
        start, stop,
        dismissError: () => { setError(null); setMissingModel(null); setDownloadError(null); setLimited(false); },
        download,
        level: () => run.current?.mic.level() ?? 0,
    };
}
