// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Which chat events should alert the user, and how (#5226). Pure, so it tests without a DOM and the phone app can import it. */

import type { Translate } from '../i18n/translator';
import type { MessageKey } from '../i18n/registry';
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

const EVENT_TITLE: Record<AlertEvent, MessageKey> = {
    needs_approval: 'chatAlert.needsApproval',
    question: 'chatAlert.question',
    finished: 'chatAlert.finished',
};

/** For `finished`, `session` must be fresh: its `last_run_status` is that turn's outcome. */
export function alertText(event: AlertEvent, session: Session | undefined, t: Translate): { title: string; body: string } {
    const chat = session?.title || t('chatAlert.untitled');
    if (event !== 'finished') return { title: t(EVENT_TITLE[event]), body: chat };
    switch (session?.last_run_status) {
        case 'done': return { title: t(EVENT_TITLE.finished), body: chat };
        case 'failed': return { title: t('chatAlert.failed'), body: chat };
        case 'stopped': return { title: t('chatAlert.stopped'), body: chat };
        default: return { title: t('chatAlert.ended'), body: chat };
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

/** 1 → '1st', 2 → '2nd', 11 → '11th'. */
export function ordinal(n: number): string {
    const tens = n % 100;
    const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
    return `${n}${suffix}`;
}
