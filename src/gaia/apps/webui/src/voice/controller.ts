// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Voice mode's turn loop: listen, send each utterance as an ordinary chat
 * message with `reply_style: "voice"`, speak the reply's spoken block, and let
 * the user talk over it.
 *
 * The chat view owns sending and stopping (`VoiceHost`); this owns the socket,
 * the microphone and the speaker, and writes what the screen shows into
 * `useVoiceStore`.
 */

import { toolRowText } from '../components/AgentActivity';
import { getT, tEn } from '../i18n/useT';
import { ApiError } from '../services/api';
import { downloadVoiceModel, getVoiceStatus, type VoiceStatus } from '../services/voiceApi';
import { useChatStore } from '../stores/chatStore';
import { useVoiceStore, VOICE_IDLE, type VoiceLogRow, type VoicePhase } from '../stores/voiceStore';
import { log } from '../utils/logger';
import { createVoiceContext, MicCapture, micErrorCode, micSupported, SpeechPlayer } from './audio';
import { isVoiceErrorCode, type ServerEvent, type SpeakKind, type VoiceFailure } from './protocol';
import { VoiceSocket } from './socket';
import { VoiceTurnTap, type TurnOutcome } from './turnTap';

export interface VoiceHost {
    /** Send `text` as this chat's next message, spoken style. False when the chat can't take it now. */
    sendTurn: (text: string, tap: VoiceTurnTap) => boolean;
    /** Stop the running turn, as the Stop button does. */
    stopTurn: () => void;
}

/** Everything that lives only while the screen is open. */
interface Live {
    ctx: AudioContext;
    mic: MicCapture;
    player: SpeechPlayer;
    socket: VoiceSocket | null;
    host: VoiceHost;
    status: VoiceStatus | null;
    userPaused: boolean;
    /** The server said its speech for now is all sent. */
    speechSent: boolean;
    /** Utterances heard while a turn was still running, sent when it ends. */
    pending: string[];
    /** Where each spoken piece starts on the playback timeline. */
    pieces: Array<{ kind: SpeakKind; words: number; start: number }>;
    spokenSoFar: string;
    spokeThisTurn: boolean;
    answerRow: number | null;
    ticker: ReturnType<typeof setInterval> | null;
    wakeLock: { release: () => Promise<void> } | null;
    phone: boolean;
}

const PHONE_NOTICE_KEY = 'gaia:voice:phone-notice-seen';

let live: Live | null = null;
let rowId = 0;

const set = useVoiceStore.setState;
const get = useVoiceStore.getState;

const words = (text: string) => text.split(/\s+/).filter(Boolean).length;
const collapse = (text: string) => text.replace(/\s+/g, ' ').trim();

function addRow(row: Omit<VoiceLogRow, 'id'>): number {
    const id = ++rowId;
    set((s) => ({ log: [...s.log, { ...row, id }] }));
    return id;
}

function patchRow(id: number, patch: Partial<VoiceLogRow>): void {
    set((s) => ({ log: s.log.map((r) => (r.id === id ? { ...r, ...patch } : r)) }));
}

function note(key: Parameters<ReturnType<typeof getT>>[0]): void {
    addRow({ kind: 'note', text: getT()(key) });
}

/** Where the loop rests when nobody has the floor. */
function resting(): VoicePhase {
    if (!live) return 'paused';
    if (live.userPaused) return 'paused';
    if (get().pushToTalk) return get().holding ? 'listening' : 'ptt_idle';
    return 'listening';
}

function speaks(): boolean {
    return !!live?.status?.speaks;
}

/** Light caption words as their audio plays. */
function tick(): void {
    if (!live) return;
    const caption = get().caption;
    if (!caption || caption.lit === Infinity) return;
    const { pieces, player } = live;
    const mine = pieces.filter((p) => p.kind === caption.kind);
    let lit = 0;
    for (let i = 0; i < mine.length; i++) {
        const p = mine[i];
        const next = pieces[pieces.indexOf(p) + 1];
        const end = next ? next.start : player.pushed;
        if (player.played >= end) lit += p.words;
        else if (player.played > p.start && end > p.start) lit += Math.floor(p.words * (player.played - p.start) / (end - p.start));
    }
    if (lit !== caption.lit) set({ caption: { ...caption, lit: Math.min(lit, words(caption.text)) } });
}

function startTicker(): void {
    if (live && !live.ticker) live.ticker = setInterval(tick, 80);
}

function stopTicker(): void {
    if (live?.ticker) clearInterval(live.ticker);
    if (live) live.ticker = null;
}

function finishSpeakingIfDone(): void {
    if (!live || !live.speechSent || live.player.playing) return;
    live.speechSent = false;
    stopTicker();
    const caption = get().caption;
    if (caption) set({ caption: { ...caption, lit: Infinity } });
    if (get().phase === 'speaking') set({ phase: get().turnRunning ? 'thinking' : resting() });
}

/** Barge-in: silence GAIA here first, then tell the server. */
function bargeIn(): void {
    if (!live) return;
    live.player.flush();
    live.socket?.interrupt();
    live.speechSent = false;
    stopTicker();
    const caption = get().caption;
    if (caption) set({ caption: { ...caption, lit: Infinity } });
}

function startTurn(text: string): void {
    if (!live) return;
    live.spokenSoFar = '';
    live.spokeThisTurn = false;
    live.answerRow = null;
    live.pieces = [];
    set({ turnRunning: true, phase: 'thinking', caption: null });
    // A turn outlives the screen when voice mode closes mid-answer; it then writes nothing here.
    const mine = live;
    const own = <A extends unknown[]>(fn: (...args: A) => void) => (...args: A) => { if (live === mine) fn(...args); };
    const tap = new VoiceTurnTap({
        spokenPiece: own((piece) => {
            if (!live) return;
            live.spokenSoFar += piece;
            live.spokeThisTurn = true;
            const shown = collapse(live.spokenSoFar);
            if (speaks()) {
                live.socket?.speak(piece, 'spoken');
                const caption = get().caption;
                set({ caption: { kind: 'spoken', text: shown, lit: caption?.kind === 'spoken' ? caption.lit : 0 } });
            } else {
                set({ caption: { kind: 'spoken', text: shown, lit: Infinity } });
            }
        }),
        spokenComplete: own(() => {
            if (speaks()) live?.socket?.speakEnd();
        }),
        statusLine: own((line) => {
            addRow({ kind: 'status', text: line });
            if (!live) return;
            if (speaks() && live.status?.status_updates) live.socket?.speak(line, 'status');
            else if (!speaks()) set({ caption: { kind: 'status', text: line, lit: Infinity } });
        }),
        toolStarted: own((step) => {
            addRow({ kind: 'tool', text: '', tool: step.tool, label: toolRowText(step, getT()), done: false });
            set((s) => ({ tools: s.tools + 1 }));
        }),
        needsYou: own(() => {
            note('voice.needsYou');
            if (speaks()) live?.socket?.speak(tEn('voice.needsYou'), 'status');
            else set({ caption: { kind: 'status', text: getT()('voice.needsYou'), lit: Infinity } });
        }),
        toolFinished: own((result, ok) => {
            const row = [...get().log].reverse().find((r) => r.kind === 'tool' && !r.done);
            if (row) patchRow(row.id, { done: true, ok, ...(result ? { result } : {}) });
        }),
        detail: own((detail) => {
            if (!live) return;
            const text = detail.trim();
            if (!text) {
                if (live.answerRow !== null) set((s) => ({ log: s.log.filter((r) => r.id !== live?.answerRow) }));
                live.answerRow = null;
            } else if (live.answerRow === null) {
                live.answerRow = addRow({ kind: 'answer', text });
            } else {
                patchRow(live.answerRow, { text });
            }
        }),
        finished: own((outcome: TurnOutcome) => endTurn(outcome)),
    });
    if (!live.host.sendTurn(text, tap)) {
        // The chat is busy with a typed message; this goes when it ends.
        live.pending.push(text);
        set({ turnRunning: false, phase: resting() });
    }
}

function endTurn(outcome: TurnOutcome): void {
    if (!live) return;
    set({ turnRunning: false });
    if (outcome.failed) {
        note('voice.turnFailed');
        bargeIn();
    } else if (outcome.stopped) {
        note('voice.stopped');
        bargeIn();
    } else if (!live.spokeThisTurn) {
        if (outcome.missing) log.ui.warn('voice_reply.missing_spoken: the answer had no <spoken> block');
        const line = getT()('voice.missingSpoken');
        if (speaks()) {
            live.socket?.speak(tEn('voice.missingSpoken'), 'spoken');
            live.socket?.speakEnd();
            set({ caption: { kind: 'spoken', text: line, lit: 0 } });
        } else {
            set({ caption: { kind: 'spoken', text: line, lit: Infinity } });
        }
    }
    if (get().phase !== 'speaking') set({ phase: resting() });
    const next = live.pending.splice(0).join(' ').trim();
    if (next) startTurn(next);
}

function onEvent(event: ServerEvent): void {
    if (!live) return;
    switch (event.type) {
        case 'ready':
            set({ phase: resting() });
            return;
        case 'speech_started':
            if (get().phase === 'speaking' && !get().pushToTalk) {
                bargeIn();
                set({ phase: 'listening' });
            }
            set({ heard: '', heardPartial: true });
            return;
        case 'partial':
            set({ heard: event.text, heardPartial: true });
            return;
        case 'speech_stopped':
            if (!get().turnRunning && get().phase !== 'speaking') set({ phase: 'thinking' });
            return;
        case 'final': {
            const text = event.text.trim();
            set({ heard: text, heardPartial: false });
            if (!text) {
                if (!get().turnRunning && get().phase === 'thinking') set({ phase: resting() });
                return;
            }
            addRow({ kind: 'user', text });
            if (get().turnRunning) live.pending.push(text);
            else startTurn(text);
            return;
        }
        case 'speaking':
            live.speechSent = false;
            set({ phase: 'speaking' });
            startTicker();
            return;
        case 'spoken_piece': {
            live.pieces.push({ kind: event.kind, words: words(event.text), start: live.player.pushed });
            if (event.kind === 'status') set({ caption: { kind: 'status', text: collapse(event.text), lit: 0 } });
            else if (get().caption?.kind !== 'spoken') set({ caption: { kind: 'spoken', text: collapse(live.spokenSoFar || event.text), lit: 0 } });
            return;
        }
        case 'speaking_done':
            live.speechSent = true;
            live.player.end();
            finishSpeakingIfDone();
            return;
        case 'flush':
            live.player.flush();
            return;
        case 'error':
            // Only tts_failed reaches here; the session goes on in text.
            note('voice.error.tts_failed');
            bargeIn();
            if (get().phase === 'speaking') set({ phase: get().turnRunning ? 'thinking' : resting() });
            return;
        case 'level':
        case 'speech_stopped_by_user':
        case 'limit':
            return;
    }
}

function fail(failure: VoiceFailure): void {
    log.ui.warn(`Voice mode stopped: ${failure.code}`, failure.detail ?? '');
    if (live) {
        live.mic.stop();
        live.player.flush();
        live.socket?.close();
        live.socket = null;
        stopTicker();
    }
    set({ error: failure, phase: 'paused' });
}

function failureFromStatus(status: VoiceStatus): VoiceFailure {
    const code = isVoiceErrorCode(status.error_code) ? status.error_code : 'bad_request';
    const missing = status.models.filter((m) => !m.downloaded).map((m) => m.id).join(', ');
    return { code, detail: code === 'model_missing' ? missing : status.error ?? undefined };
}

async function connect(): Promise<void> {
    if (!live) return;
    const me = live;
    const locale = useChatStore.getState().locale;
    set({ error: null, phase: 'warming', downloadError: null });
    if (!micSupported()) {
        fail({ code: 'mic_missing', detail: 'this page has no microphone access (a secure context is required)' });
        return;
    }
    const micReady = me.mic.start().then(() => null, (err: unknown) => err);
    let status: VoiceStatus;
    try {
        status = await getVoiceStatus(locale);
    } catch (err) {
        if (live !== me) return;
        fail({ code: 'lemonade_unreachable', detail: err instanceof Error ? err.message : String(err) });
        return;
    }
    if (live !== me) return;
    me.status = status;
    set({
        status,
        pushToTalk: status.turn_taking === 'push_to_talk',
        captions: status.speaks ? get().captions : true,
    });
    const micError = await micReady;
    if (live !== me) return;
    if (micError) {
        fail({ code: micErrorCode(micError), detail: micError instanceof Error ? micError.message : String(micError) });
        return;
    }
    if (!status.ready) {
        fail(failureFromStatus(status));
        return;
    }
    if (get().pushToTalk || me.userPaused) me.mic.pause();
    me.socket = new VoiceSocket('voice', status.language, {
        onEvent,
        onAudio: (pcm) => { if (live === me) me.player.push(pcm); },
        onFailure: (failure) => { if (live === me) fail(failure); },
    });
}

async function holdWakeLock(): Promise<void> {
    const nav = navigator as Navigator & { wakeLock?: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> } };
    if (!live || !nav.wakeLock) return;
    try {
        live.wakeLock = await nav.wakeLock.request('screen');
    } catch (err) {
        // Refused (battery saver, page hidden): the screen may dim; voice keeps working.
        log.ui.warn('Screen wake lock refused', err);
    }
}

function onVisibility(): void {
    if (!live?.phone) return;
    if (document.visibilityState === 'hidden') {
        if (get().phase !== 'paused') {
            live.userPaused = true;
            live.mic.pause();
            set({ phase: 'paused' });
        }
    } else {
        void holdWakeLock();
    }
}

/** Open voice mode on a chat. Call from the click or tap itself: it creates the AudioContext. */
export function openVoice(sessionId: string, host: VoiceHost, { phone = false } = {}): void {
    if (live) closeVoice();
    const ctx = createVoiceContext();
    const player = new SpeechPlayer(ctx);
    const mic = new MicCapture(ctx, (frame) => live?.socket?.sendAudio(frame));
    live = {
        ctx, mic, player, socket: null, host, status: null, userPaused: false, speechSent: false, pending: [],
        pieces: [], spokenSoFar: '', spokeThisTurn: false, answerRow: null, ticker: null, wakeLock: null, phone,
    };
    player.onIdle = finishSpeakingIfDone;
    let noticeSeen = true;
    try {
        noticeSeen = localStorage.getItem(PHONE_NOTICE_KEY) === '1';
    } catch {
        noticeSeen = false; // storage blocked: show it, once per open
    }
    set({ ...VOICE_IDLE, open: true, sessionId, phoneNotice: phone && window.location.protocol === 'https:' && !noticeSeen });
    document.addEventListener('visibilitychange', onVisibility);
    if (phone) void holdWakeLock();
    void connect();
}

export function closeVoice(): void {
    if (!live) {
        set({ ...VOICE_IDLE });
        return;
    }
    const me = live;
    live = null;
    stopTickerFor(me);
    me.socket?.close();
    me.mic.stop();
    me.player.close();
    void me.ctx.close();
    void me.wakeLock?.release().catch((err: unknown) => log.ui.warn('Wake lock release failed', err));
    document.removeEventListener('visibilitychange', onVisibility);
    set({ ...VOICE_IDLE });
}

function stopTickerFor(me: Live): void {
    if (me.ticker) clearInterval(me.ticker);
    me.ticker = null;
}

/** The big button: pause, resume, interrupt or stop, by state. */
export function primaryAction(): void {
    if (!live) return;
    const phase = get().phase;
    if (phase === 'speaking') {
        bargeIn();
        set({ phase: get().turnRunning ? 'thinking' : resting() });
    } else if (phase === 'thinking') {
        if (get().turnRunning) live.host.stopTurn();
        bargeIn();
    } else if (phase === 'listening' && !get().pushToTalk) {
        live.userPaused = true;
        live.mic.pause();
        set({ phase: 'paused', heardPartial: false });
    } else if (phase === 'paused') {
        live.userPaused = false;
        if (get().error) {
            retryVoice();
            return;
        }
        if (!get().pushToTalk) live.mic.resume();
        set({ phase: resting() });
    }
}

/** Push-to-talk: hold. */
export function holdStart(): void {
    if (!live || !get().pushToTalk || get().holding || get().error) return;
    if (get().phase === 'speaking') bargeIn();
    live.userPaused = false;
    live.mic.resume();
    set({ holding: true, phase: 'listening', heard: '', heardPartial: true });
}

/** Push-to-talk: release ends the utterance now. */
export function holdEnd(): void {
    if (!live || !get().holding) return;
    live.mic.pause();
    live.socket?.commit();
    set({ holding: false, phase: 'thinking' });
}

export function retryVoice(): void {
    if (!live) return;
    live.socket?.close();
    live.socket = null;
    live.mic.stop();
    live.mic = new MicCapture(live.ctx, (frame) => live?.socket?.sendAudio(frame));
    void live.ctx.resume();
    void connect();
}

/** Download one missing speech model (after the user clicked), then start again. */
export async function downloadModel(model: string): Promise<void> {
    if (!live || get().downloading) return;
    set({ downloading: model, downloadError: null });
    try {
        await downloadVoiceModel(model);
    } catch (err) {
        set({ downloading: null, downloadError: err instanceof ApiError ? err.detail || err.message : String(err) });
        return;
    }
    set({ downloading: null });
    if (!live) return;
    const status = await getVoiceStatus(useChatStore.getState().locale).catch((err: unknown) => {
        set({ downloadError: err instanceof Error ? err.message : String(err) });
        return null;
    });
    if (!status) return;
    set({ status });
    if (status.ready) retryVoice();
    else set({ error: failureFromStatus(status) });
}

export function setCaptions(on: boolean): void {
    if (!speaks() && live?.status) return; // shown, not spoken: captions stay on
    set({ captions: on });
}

export function dismissPhoneNotice(): void {
    try {
        localStorage.setItem(PHONE_NOTICE_KEY, '1');
    } catch (err) {
        log.ui.warn('Could not remember the voice notice', err);
    }
    set({ phoneNotice: false });
}

/** The chat finished a typed turn: send what was said meanwhile. */
export function chatIdle(): void {
    if (!live || get().turnRunning || live.pending.length === 0) return;
    const next = live.pending.splice(0).join(' ').trim();
    if (next) startTurn(next);
}

/** Levels the shape follows; 0 when nothing is live. */
export const voiceLevels = {
    mic: (): number => live?.mic.level() ?? 0,
    speech: (): number => live?.player.level() ?? 0,
};

/** For tests: drive the loop as the socket would. */
export const __voiceTest = { onEvent, fail, endTurn, startTurn, isLive: () => live !== null };
