// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Phone alerts: the UI server forwards these to the daemon (`gaia/ui/routers/phone.py`). */

import { getApiBase } from '../utils/apiBase';
import { ApiError, UI_HEADER, apiFetch } from './api';

export type PhoneProvider = 'simulated' | 'twilio';
export type PhoneSteps = 'notify' | 'notify_text' | 'notify_text_call';
export type PhoneRuleKey = 'schedule_failed' | 'approval_waiting' | 'gaia_down' | 'agent';
export type SimulatedOutcome = 'human' | 'machine_start' | 'no-answer' | 'busy';

export const PHONE_RULES: PhoneRuleKey[] = ['schedule_failed', 'approval_waiting', 'gaia_down', 'agent'];
export const PHONE_STEPS: PhoneSteps[] = ['notify', 'notify_text', 'notify_text_call'];
export const SIMULATED_OUTCOMES: SimulatedOutcome[] = ['human', 'machine_start', 'no-answer', 'busy'];

/** Mirrors `gaia/phone/settings.py`. */
export const LIMIT_RANGES = { calls_per_day: [0, 20], texts_per_day: [0, 50], agent_calls_per_day: [0, 5] } as const;
export const AFTER_MINUTES_RANGE = [1, 240] as const;
export type PhoneLimit = keyof typeof LIMIT_RANGES;

export interface PhoneContact {
    id: number;
    label: string;
    /** Masked, e.g. `+1 •••• 4567`. */
    number: string;
    primary: boolean;
}

export interface PhoneStatus {
    enabled: boolean;
    provider: PhoneProvider;
    configured: boolean;
    from_number: string;
    texts_enabled: boolean;
    contacts: PhoneContact[];
    open_alerts: number;
    calls_today: number;
    texts_today: number;
    last_error: string | null;
    /** Whether the PC is held awake for calls; null when keep awake is off. */
    awake: { held: boolean; reason: string } | null;
}

export interface PhoneRule {
    on: boolean;
    steps: PhoneSteps;
    urgent: boolean;
    after_minutes?: number;
}

export interface PhoneSettings {
    enabled: boolean;
    provider: PhoneProvider;
    from_number: string;
    texts_enabled: boolean;
    quiet_hours: { start: string; end: string } | null;
    limits: Record<PhoneLimit, number>;
    delays: { text_after_minutes: number; call_after_minutes: number };
    cooldown_minutes: number;
    simulated_outcome: SimulatedOutcome;
    keep_awake: boolean;
    rules: Record<PhoneRuleKey, PhoneRule>;
    /** Whether a call PIN is set; the PIN itself never leaves the daemon. */
    pin_set: boolean;
    /** Mobile access is on and the PC published where calls connect. */
    talk_available: boolean;
    /** After the alert sentence, connect the call so you can talk with GAIA. */
    talk_on_calls: boolean;
    /** Kokoro voice GAIA speaks in on calls. */
    voice: string;
}

/** Only the keys that change; nested objects carry only their changed fields. */
export interface PhoneSettingsPatch {
    enabled?: boolean;
    provider?: PhoneProvider;
    texts_enabled?: boolean;
    quiet_hours?: { start: string; end: string } | null;
    limits?: Partial<Record<PhoneLimit, number>>;
    simulated_outcome?: SimulatedOutcome;
    keep_awake?: boolean;
    rules?: Partial<Record<PhoneRuleKey, Partial<PhoneRule>>>;
    talk_on_calls?: boolean;
    voice?: string;
}

export interface PhoneDelivery {
    channel: 'call' | 'text';
    number: string;
    state: string;
    outcome: string | null;
    reached: boolean;
    error: string | null;
    at: number;
}

export interface PhoneAlert {
    id: number;
    source: string;
    kind: string;
    title: string;
    reason: string;
    count: number;
    state: 'open' | 'handled' | string;
    urgent: boolean;
    created_at: number;
    reached: boolean;
    held: string;
    deliveries: PhoneDelivery[];
}

export interface CodeStarted {
    verification_id: string;
    number: string;
    needs_confirm_code?: boolean;
    confirm_sent_to?: string | null;
    expires_in?: number;
}

export interface SimulatedLog {
    calls: Array<{ sid: string; to: string; from: string; script: string }>;
    texts: Array<{ sid: string; to: string; from: string; body: string }>;
}

export const getPhoneStatus = () => apiFetch<PhoneStatus>('GET', '/phone/status');
/**
 * Status for the open-alert poll: null while the background service is down or
 * outdated (503), without the console error apiFetch logs, since the poll
 * repeats every 15 seconds. Any other failure throws.
 */
export async function pollPhoneStatus(): Promise<PhoneStatus | null> {
    const res = await fetch(`${getApiBase()}/phone/status`, { headers: { ...UI_HEADER } });
    if (res.status === 503) return null;
    if (!res.ok) throw new ApiError(`GET /phone/status failed (HTTP ${res.status})`, res.status, await res.text().catch(() => ''), null);
    return res.json() as Promise<PhoneStatus>;
}
export const getPhoneSettings = () => apiFetch<PhoneSettings>('GET', '/phone/settings');
export const updatePhoneSettings = (patch: PhoneSettingsPatch) => apiFetch<PhoneSettings>('PUT', '/phone/settings', patch);
export const setTwilioKeys = (account_sid: string, auth_token: string, from_number: string) =>
    apiFetch<PhoneStatus>('POST', '/phone/keys', { account_sid, auth_token, from_number });
export const forgetTwilioKeys = () => apiFetch<PhoneStatus>('DELETE', '/phone/keys');
export const useSimulatedPhone = () => apiFetch<PhoneStatus>('POST', '/phone/simulated');
/** 4 to 8 digits. Returns the public settings; nothing is kept in the page. */
export const setPhonePin = (pin: string) => apiFetch<PhoneSettings>('POST', '/phone/pin', { pin });
export const clearPhonePin = () => apiFetch<PhoneSettings>('DELETE', '/phone/pin');
export const getSimulatedLog = () => apiFetch<SimulatedLog>('GET', '/phone/simulated/log');
export const addPhoneNumber = (number: string, label: string, via: 'call' | 'text') =>
    apiFetch<CodeStarted>('POST', '/phone/contacts', { number, label, via });
export const confirmPhoneNumber = (verification_id: string, code: string, confirm_code?: string) =>
    apiFetch<PhoneContact>('POST', '/phone/contacts/confirm', { verification_id, code, ...(confirm_code ? { confirm_code } : {}) });
export const removePhoneNumber = (id: number) => apiFetch<CodeStarted>('POST', `/phone/contacts/${id}/remove`);
export const confirmRemovePhoneNumber = (verification_id: string, code: string) =>
    apiFetch<PhoneStatus>('POST', '/phone/contacts/remove/confirm', { verification_id, code });
export const getPhoneAlerts = (openOnly = false) =>
    apiFetch<{ alerts: PhoneAlert[] }>('GET', `/phone/alerts${openOnly ? '?open=true' : ''}`);
export const markAlertHandled = (id: number) => apiFetch<PhoneAlert>('POST', `/phone/alerts/${id}/handled`);
export const sendTestAlert = () => apiFetch<{ alert: PhoneAlert }>('POST', '/phone/test');

/** Codes from `gaia/phone/errors.py`, plus the UI server's own. */
export const PHONE_ERROR_CODES = [
    'bad_number', 'not_in_phone_book', 'not_configured', 'phone_off', 'bad_credentials', 'provider_error',
    'provider_unreachable', 'trial_unverified', 'verification_failed', 'verification_expired', 'rate_limited',
    'limit_reached', 'last_number', 'duplicate_number', 'not_found', 'bad_request',
    'daemon_unavailable', 'daemon_outdated', 'local_only',
] as const;
export type PhoneErrorCode = (typeof PHONE_ERROR_CODES)[number];

export interface PhoneFailure {
    code: PhoneErrorCode | 'unknown';
    /** The service's own sentence: shown under the translated one where it adds something (Twilio's words). */
    message: string;
    /** Tries left on a wrong code, when the service said. */
    triesLeft?: number;
}

/** A failed phone request, by its stable code. A 403 is a change refused over mobile access. */
export function phoneFailure(err: unknown): PhoneFailure {
    if (err instanceof ApiError) {
        const code = typeof err.body?.code === 'string' ? err.body.code : null;
        const message = typeof err.body?.message === 'string' ? err.body.message : err.detail || err.message;
        if (!code && err.httpStatus === 403) return { code: 'local_only', message };
        if (code && (PHONE_ERROR_CODES as readonly string[]).includes(code)) {
            const left = typeof err.body?.tries_left === 'number' ? err.body.tries_left : null;
            return { code: code as PhoneErrorCode, message, ...(left ? { triesLeft: left } : {}) };
        }
        return { code: 'unknown', message };
    }
    return { code: 'unknown', message: err instanceof Error ? err.message : String(err) };
}

/** What a simulated call said: the text inside its `<Say>` tags. */
export function spokenText(script: string): string {
    const said = [...script.matchAll(/<Say[^>]*>([\s\S]*?)<\/Say>/g)].map((m) => m[1]);
    const text = (said.length ? said[0] : script.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
    return text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'");
}

/** The last four digits only, like the phone book shows. */
export function maskNumber(number: string): string {
    return number.length > 6 ? `${number.slice(0, 2)} •••• ${number.slice(-4)}` : '••••';
}
