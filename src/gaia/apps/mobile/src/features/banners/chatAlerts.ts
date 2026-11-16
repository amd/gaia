// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { chatAlertEvents, chatPhases, type ChatAlert, type ChatPhases } from '@webui/utils/chatAlerts.core';

import type { ChatTurn } from '@/state/chatStreams';

/** How long a finished-chat banner stays; the web raises no in-app banner (its alerts are OS notifications), so this is the phone's own. */
export const FINISHED_ALERT_MS = 8000;

export type TurnOutcome = 'done' | 'failed' | 'stopped';

export interface ChatAlertWithOutcome extends ChatAlert {
    /** How the turn ended; only a `finished` alert has one. */
    outcome?: TurnOutcome;
}

/** Each chat's phase as the alert logic sees it: running, or paused on an approval or a question. */
export function turnPhases(turns: Readonly<Record<string, ChatTurn>>): ChatPhases {
    const running: string[] = [];
    const awaiting: Record<string, 'question'> = {};
    const prompting: string[] = [];
    for (const [id, turn] of Object.entries(turns)) {
        if (turn.status === 'idle') continue;
        running.push(id);
        if (turn.pendingQuestion) awaiting[id] = 'question';
        if (turn.pendingPermission) prompting.push(id);
    }
    return chatPhases(running, awaiting, prompting);
}

/** What changed between two snapshots of the live turns that a person should hear about. */
export function alertsBetween(prev: Readonly<Record<string, ChatTurn>>, next: Readonly<Record<string, ChatTurn>>): ChatAlertWithOutcome[] {
    return chatAlertEvents(turnPhases(prev), turnPhases(next)).map((alert) => {
        if (alert.event !== 'finished') return alert;
        const ended = prev[alert.sessionId] ?? next[alert.sessionId];
        const outcome: TurnOutcome = ended?.failed || ended?.error ? 'failed' : ended?.stopState ? 'stopped' : 'done';
        return { ...alert, outcome };
    });
}
