// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Automations: what GAIA runs on its own (`/api/automations`). */

import { apiFetch, UI_HEADER } from './api';

export type AutomationKind = 'reminder' | 'summary' | 'alert' | 'trigger';

export interface Automation {
    id: string;
    name: string;
    kind: AutomationKind;
    /** An alert named "Heartbeat": the user's check-in checklist. */
    heartbeat: boolean;
    paused: boolean;
    /** 0 for a one-time reminder. */
    every_seconds: number;
    /** Epoch seconds. */
    next_at: number;
    last_run_at: number;
    runs: number;
    prompt: string;
    say: string;
    check: string;
    quiet: string;
    days: string;
    /** A trigger's failed checks in a row. */
    failures: number;
}

export interface AutomationList {
    automations: Automation[];
    /** False when no paired Discord DM would receive what they send; null when that couldn't be checked. */
    delivers: boolean | null;
}

export function listAutomations(): Promise<AutomationList> {
    return apiFetch('GET', '/automations');
}

export function setAutomationPaused(id: string, paused: boolean): Promise<Automation> {
    return apiFetch('POST', `/automations/${encodeURIComponent(id)}/${paused ? 'pause' : 'resume'}`, undefined, UI_HEADER);
}

export function removeAutomation(id: string): Promise<Automation> {
    return apiFetch('DELETE', `/automations/${encodeURIComponent(id)}`, undefined, UI_HEADER);
}
