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
    /** Answer this chat's open permission prompt. False when there is none. */
    answerPermission: (allow: boolean) => boolean;
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
    /** The running turn is being stopped because the user said something new. */
    replacing: boolean;
    /** GAIA asked for a yes or no and is waiting to hear it. */
    asking: boolean;
    /** Runs out when the user's hold on GAIA's attention does. */
    attention: ReturnType<typeof setTimeout> | null;
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
    attend();
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

const NO = /\b(no|nope|don'?t|do not|stop|cancel|deny|never ?mind)\b/i;
const YES = /\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|go for it|allow|approve|please)\b/i;

/** A spoken answer to "yes or no?": true, false, or null when it was neither. */
export function yesOrNo(text: string): boolean | null {
    if (NO.test(text)) return false;
    if (YES.test(text)) return true;
    return null;
}

const QUIET = /\b(don'?t|do not|stop|quit|no need to) (respond|answer|reply|talk|speak|listen)(ing)?\b|\bnot talking to you\b|\b(be|stay|keep) quiet\b|\bignore (us|me|this|that)\b|\bgo to sleep\b|\bstand ?by\b|\bavoid speaking\b/i;
const NAME = /\b(?:(?:hey|hi|hello|ok|okay|yo)[\s,]+)?(?:gaia|gaya|gaea|gaiya|kaia|guya)\b[\s,.!?:-]*/i;

/** How long after GAIA speaks a reply needs no "Hey GAIA". */
export const FOLLOW_UP_MS = 10_000;

function wakeWord(): boolean {
    return !!live?.status?.wake_word && !get().pushToTalk;
}

/** The user has GAIA's attention: now, and for a while after she is done. */
function attend(): void {
    if (!live) return;
    if (live.attention) clearTimeout(live.attention);
    live.attention = null;
    if (get().standby) set({ standby: false });
    if (!wakeWord()) return;
    const mine = live;
    live.attention = setTimeout(() => {
        if (live !== mine) return;
        mine.attention = null;
        // She is still working or talking: the clock starts when she is done.
        if (get().turnRunning || get().phase === 'speaking' || mine.asking) attend();
        else set({ standby: true });
    }, FOLLOW_UP_MS);
}

/** Nothing is sent until someone says her name. */
function standBy(): void {
    if (!live) return;
    if (live.attention) clearTimeout(live.attention);
    live.attention = null;
    set({ standby: true });
}

/** Mute or unmute the microphone; GAIA keeps talking and working. */
export function toggleMute(): void {
    if (!live || get().error || get().pushToTalk) return;
    live.userPaused = !live.userPaused;
    set({ muted: live.userPaused });
    if (live.userPaused) {
        live.mic.pause();
        if (get().phase === 'listening') set({ phase: 'paused', heardPartial: false });
    } else {
        live.mic.resume();
        if (get().phase === 'paused') set({ phase: resting() });
        attend();
    }
}

/** Whether `text` asks GAIA to stay out of the conversation. */
export function asksForQuiet(text: string): boolean {
    return QUIET.test(text) && !NAME.test(text.replace(QUIET, ''));
}

/** What follows GAIA's name (maybe nothing); null when she was not addressed. */
export function afterName(text: string): string | null {
    const hit = NAME.exec(text);
    return hit ? text.slice(hit.index + hit[0].length).trim() : null;
}

const STOP_LEAD = /^\W*(stop|wait|cancel|never ?mind|hold on|hang on|actually|no)\b[\s,.!-]*/i;

/** What follows a leading "stop" or "wait" (maybe nothing); null when `text` is not telling GAIA to stop. */
export function afterStopWord(text: string): string | null {
    const lead = STOP_LEAD.exec(text);
    if (!lead) return null;
    let rest = text.slice(lead[0].length);
    // "no, wait, stop" is still only a stop.
    for (let more = STOP_LEAD.exec(rest); more; more = STOP_LEAD.exec(rest)) rest = rest.slice(more[0].length);
    return rest.trim();
}

const IMAGE_PATH = /((?:[A-Za-z]:\\|\/)[^\n"<>|]*?\.(?:png|jpe?g|webp|gif))/i;

/** The picture a tool result names, if any. */
export function imageIn(result: string): string | undefined {
    return IMAGE_PATH.exec(result)?.[1];
}

function setAsking(on: boolean): void {
    if (!live || live.asking === on) return;
    live.asking = on;
    live.socket?.expect(on);
}

/** Say a fixed line out loud, or caption it when this language has no voice. */
function say(key: Parameters<ReturnType<typeof getT>>[0]): void {
    if (speaks()) live?.socket?.speak(tEn(key), 'status');
    else set({ caption: { kind: 'status', text: getT()(key), lit: Infinity } });
}

const BACKCHANNELS = new Set(['yeah', 'yep', 'yes', 'ok', 'okay', 'right', 'sure', 'mhm', 'mm', 'hmm', 'uh', 'huh', 'i', 'see', 'got', 'it', 'cool', 'nice', 'great', 'wow', 'oh', 'ah']);
const FLOOR_WORDS = new Set(['stop', 'wait', 'no', 'hold', 'cancel', 'pause', 'quiet', 'enough']);

/** Whether `text` is only a listener's acknowledgment (mirrors gaia.voice.turns). */
export function isBackchannel(text: string): boolean {
    const tokens = text.toLowerCase().match(/[a-z0-9']+/g) ?? [];
    if (tokens.length === 0 || tokens.length > 3) return false;
    if (tokens.some((t) => FLOOR_WORDS.has(t))) return false;
    return tokens.every((t) => BACKCHANNELS.has(t));
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
        needsYou: own((kind) => {
            // A permission can be answered out loud; a question card is answered on screen.
            const key = kind === 'permission' ? 'voice.askYesNo' : 'voice.needsYou';
            if (kind === 'permission') setAsking(true);
            note(key);
            say(key);
        }),
        toolFinished: own((result, ok) => {
            setAsking(false);
            const row = [...get().log].reverse().find((r) => r.kind === 'tool' && !r.done);
            const image = ok ? imageIn(result) : undefined;
            if (row) patchRow(row.id, { done: true, ok, ...(result ? { result } : {}), ...(image ? { image } : {}) });
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
    setAsking(false);
    set({ turnRunning: false });
    if (outcome.failed) {
        note('voice.turnFailed');
        bargeIn();
    } else if (outcome.stopped) {
        // Stopped by the user's own new words: that turn follows, so no notice.
        if (!live.replacing) note('voice.stopped');
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
    live.replacing = false;
    if (get().phase !== 'speaking') set({ phase: resting() });
    attend();
    const next = live.pending.splice(0).join(' ').trim();
    if (next) startTurn(next);
}

function onEvent(event: ServerEvent): void {
    if (!live) return;
    switch (event.type) {
        case 'ready':
            set({ phase: resting() });
            attend();
            return;
        case 'speech_started':
            // A sound is not yet an interruption: the server ducks, then decides.
            set({ heard: '', heardPartial: true });
            return;
        case 'duck':
            live.player.duck(event.on);
            return;
        case 'barge_in':
            live.player.flush();
            live.speechSent = false;
            stopTicker();
            if (get().caption) set({ caption: { ...get().caption!, lit: Infinity } });
            if (get().phase === 'speaking') set({ phase: 'listening' });
            return;
        case 'holding':
            // Trailed off ("and…"): keep listening, show what was heard so far.
            set({ heard: event.text, heardPartial: true });
            if (!get().turnRunning && get().phase === 'thinking') set({ phase: resting() });
            return;
        case 'ignored':
            // Typing or a cough: nothing was said.
            set({ heard: '', heardPartial: false });
            if (!get().turnRunning && get().phase === 'thinking') set({ phase: resting() });
            return;
        case 'recovering':
            log.ui.warn('Voice: the recognizer dropped; reopening it');
            if (!get().turnRunning && get().phase !== 'speaking') set({ phase: 'warming', heardPartial: false });
            return;
        case 'recovered':
            if (get().phase === 'warming') set({ phase: resting() });
            return;
        case 'timing':
            log.ui.info(`voice timing: heard ${event.heard_ms} ms, think ${event.think_ms} ms, voice ${event.voice_ms} ms, total ${event.total_ms} ms`);
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
            if (get().turnRunning && live.asking) {
                addRow({ kind: 'user', text });
                const answer = yesOrNo(text);
                if (answer === null || !live.host.answerPermission(answer)) {
                    if (answer === null) say('voice.yesOrNo');
                    else setAsking(false);
                    return;
                }
                setAsking(false);
                note(answer ? 'voice.allowed' : 'voice.denied');
                set({ phase: 'thinking' });
                return;
            }
            if (get().turnRunning) {
                // "mm-hm" while GAIA works is not a new request.
                if (isBackchannel(text)) return;
                addRow({ kind: 'user', text });
                const rest = afterStopWord(text);
                if (rest === null) {
                    // Talk in the room while she works waits its turn; it never cancels the work.
                    live.pending.push(text);
                    return;
                }
                // "Stop", "wait, make it a cat": drop the work, then do what follows.
                if (rest) live.pending.push(rest);
                if (!live.replacing) {
                    live.replacing = true;
                    bargeIn();
                    live.host.stopTurn();
                }
                return;
            }
            let said = text;
            if (get().standby) {
                // Asked to stay out of it: only her name brings her back.
                const rest = afterName(text);
                if (rest === null) {
                    set({ heard: '', heardPartial: false, phase: resting() });
                    return;
                }
                attend();
                if (!rest) {
                    addRow({ kind: 'user', text });
                    say('voice.standbyOff');
                    set({ phase: resting() });
                    return;
                }
                said = rest;
            } else if (asksForQuiet(text)) {
                addRow({ kind: 'user', text });
                standBy();
                set({ phase: resting() });
                note('voice.standbyOn');
                say('voice.standbyOn');
                return;
            }
            addRow({ kind: 'user', text: said });
            startTurn(said);
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
            live.userPaused = true; set({ muted: true });
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
        ctx, mic, player, socket: null, host, status: null, userPaused: false, speechSent: false, pending: [], replacing: false, asking: false, attention: null,
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
    if (me.attention) clearTimeout(me.attention);
    me.attention = null;
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
        live.userPaused = true; set({ muted: true });
        live.mic.pause();
        set({ phase: 'paused', heardPartial: false });
    } else if (phase === 'paused') {
        live.userPaused = false; set({ muted: false });
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
    live.userPaused = false; set({ muted: false });
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
