// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Which chat events should alert the user, and how (#5226). Pure, so it tests without a DOM. */

import type { AlertEvent, AlertSettings } from '../stores/notificationStore';
import type { AwaitingUser, Session } from '../types';

/** A chat's turn state as the alert logic sees it. */
export type ChatPhase = 'running' | 'permission' | 'question';

/** Session id → phase, for every chat with a turn in flight. */
export type ChatPhases = Record<string, ChatPhase>;

export function chatPhases(
    running: readonly string[],
    awaiting: Readonly<Record<string, AwaitingUser>>,
    prompting: readonly string[],
): ChatPhases {
    const phases: ChatPhases = {};
    for (const id of running) phases[id] = 'running';
    for (const [id, what] of Object.entries(awaiting)) phases[id] = what;
    for (const id of prompting) phases[id] = 'permission';
    return phases;
}

export interface ChatAlert {
    sessionId: string;
    event: AlertEvent;
}

/** Events between two snapshots: a new pause on the user, or a turn that ended. */
export function chatAlertEvents(prev: ChatPhases, next: ChatPhases): ChatAlert[] {
    const out: ChatAlert[] = [];
    for (const [id, phase] of Object.entries(next)) {
        if (phase === prev[id]) continue;
        if (phase === 'permission') out.push({ sessionId: id, event: 'needs_approval' });
        else if (phase === 'question') out.push({ sessionId: id, event: 'question' });
    }
    for (const id of Object.keys(prev)) {
        if (!(id in next)) out.push({ sessionId: id, event: 'finished' });
    }
    return out;
}

const EVENT_TITLE: Record<AlertEvent, string> = {
    needs_approval: 'GAIA needs your approval',
    question: 'GAIA has a question',
    finished: 'GAIA finished',
};

/** For `finished`, `session` must be fresh: its `last_run_status` is that turn's outcome. */
export function alertText(event: AlertEvent, session: Session | undefined): { title: string; body: string } {
    const chat = session?.title || 'A chat';
    if (event !== 'finished') return { title: EVENT_TITLE[event], body: chat };
    switch (session?.last_run_status) {
        case 'done': return { title: EVENT_TITLE.finished, body: chat };
        case 'failed': return { title: 'GAIA hit a problem', body: chat };
        case 'stopped': return { title: 'GAIA stopped', body: chat };
        default: return { title: 'A chat stopped running', body: chat };
    }
}

/** Whether an alert should interrupt: not while the user is looking at that chat. */
export function chatIsInView(sessionId: string, currentSessionId: string | null, focused: boolean): boolean {
    return focused && sessionId === currentSessionId;
}

/** The tray/dock badge: chats paused on you plus unread finished chats, per the settings. */
export function attentionCount(
    sessions: readonly Session[],
    phases: ChatPhases,
    modes: AlertSettings['modes'],
    currentSessionId: string | null,
): number {
    let n = 0;
    for (const s of sessions) {
        if (s.kind === 'eval' || s.kind === 'scheduled') continue;
        const phase = phases[s.id];
        if (phase === 'permission' && modes.needs_approval !== 'off') n++;
        else if (phase === 'question' && modes.question !== 'off') n++;
        else if (!phase && s.unread && s.id !== currentSessionId && modes.finished !== 'off') n++;
    }
    return n;
}

export interface BatteryState {
    charging: boolean;
    /** 0..1, as navigator.getBattery() reports it. */
    level: number;
}

/** The battery policy: on battery below the floor, let the PC sleep. */
export function batteryAllowsKeepAwake(battery: BatteryState | null, floorPercent: number): boolean {
    if (!battery || battery.charging || floorPercent <= 0) return true;
    return battery.level * 100 >= floorPercent;
}

/** A short two-note chime. Throws when the browser has no Web Audio. */
export function playChime(): void {
    const Ctx = window.AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) throw new Error('This browser has no Web Audio, so GAIA cannot play the alert sound.');
    const ctx = new Ctx();
    const t = ctx.currentTime;
    [660, 880].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t + i * 0.12);
        gain.gain.exponentialRampToValueAtTime(0.15, t + i * 0.12 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.12 + 0.18);
        osc.connect(gain).connect(ctx.destination);
        osc.start(t + i * 0.12);
        osc.stop(t + i * 0.12 + 0.2);
    });
    setTimeout(() => void ctx.close(), 600);
}

/** 1 → '1st', 2 → '2nd', 11 → '11th'. */
export function ordinal(n: number): string {
    const tens = n % 100;
    const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
    return `${n}${suffix}`;
}
