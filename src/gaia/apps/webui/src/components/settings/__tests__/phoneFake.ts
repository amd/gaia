// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A small stand-in for the daemon's phone routes, answering `fetch` the way the UI server would. */

import { vi } from 'vitest';
import type { PhoneAlert, PhoneSettings, PhoneStatus } from '../../../services/phoneApi';

export interface Call {
    method: string;
    path: string;
    body: unknown;
}

export interface Reply {
    status?: number;
    json: unknown;
}

const DEFAULT_SETTINGS: PhoneSettings = {
    enabled: false, provider: 'simulated', from_number: '', texts_enabled: false, quiet_hours: null,
    limits: { calls_per_day: 6, texts_per_day: 20, agent_calls_per_day: 2 },
    delays: { text_after_minutes: 5, call_after_minutes: 5 }, cooldown_minutes: 30, simulated_outcome: 'human', keep_awake: true,
    rules: {
        schedule_failed: { on: true, steps: 'notify_text_call', urgent: false },
        approval_waiting: { on: true, steps: 'notify_text', urgent: false, after_minutes: 10 },
        gaia_down: { on: true, steps: 'notify_text_call', urgent: false },
        agent: { on: false, steps: 'notify_text_call', urgent: false },
    },
};

export const phoneError = (status: number, code: string, message: string, extra: Record<string, unknown> = {}): Reply => ({ status, json: { detail: { code, message, ...extra } } });

export class FakePhone {
    calls: Call[] = [];
    settings: PhoneSettings = structuredClone(DEFAULT_SETTINGS);
    configured = false;
    contacts: Array<{ id: number; label: string; number: string }> = [];
    alerts: PhoneAlert[] = [];
    simCalls: Array<{ sid: string; to: string; from: string; script: string }> = [];
    /** One-off answers by `METHOD path`, used before the default behaviour. */
    overrides = new Map<string, Reply[]>();
    private nextId = 1;
    private pending: { purpose: 'add' | 'remove'; number?: string; label?: string; contactId?: number } | null = null;

    status(): PhoneStatus {
        return {
            enabled: this.settings.enabled, provider: this.settings.provider, configured: this.configured,
            from_number: this.settings.from_number, texts_enabled: this.settings.texts_enabled,
            contacts: this.contacts.map((c, i) => ({ id: c.id, label: c.label, number: `+1 •••• ${c.number.slice(-4)}`, primary: i === 0 })),
            open_alerts: this.alerts.filter((a) => a.state === 'open').length, calls_today: 0, texts_today: 0, last_error: null,
            awake: { held: true, reason: 'held' },
        };
    }

    once(key: string, reply: Reply): void {
        this.overrides.set(key, [...(this.overrides.get(key) ?? []), reply]);
    }

    bodies(method: string, path: string): unknown[] {
        return this.calls.filter((c) => c.method === method && c.path === path).map((c) => c.body);
    }

    private route(method: string, path: string, body: Record<string, any>): Reply {
        const key = `${method} ${path}`;
        const queued = this.overrides.get(key);
        if (queued?.length) return queued.shift()!;
        if (key === 'GET /phone/status') return { json: this.status() };
        if (key === 'GET /phone/settings') return { json: this.settings };
        if (key === 'GET /phone/alerts') return { json: { alerts: this.alerts } };
        if (key === 'GET /phone/alerts?open=true') return { json: { alerts: this.alerts.filter((a) => a.state === 'open') } };
        if (key === 'GET /phone/simulated/log') return { json: { calls: this.simCalls, texts: [] } };
        if (key === 'POST /phone/simulated') {
            this.settings.provider = 'simulated';
            this.settings.from_number = '+15550100000';
            this.configured = true;
            return { json: this.status() };
        }
        if (key === 'PUT /phone/settings') {
            const merged: any = structuredClone(this.settings);
            for (const [k, v] of Object.entries(body)) {
                if (k === 'rules') for (const [r, cfg] of Object.entries(v as object)) Object.assign(merged.rules[r], cfg);
                else if (k === 'limits') Object.assign(merged.limits, v);
                else merged[k] = v;
            }
            if (merged.enabled && !this.settings.enabled && (!this.configured || this.contacts.length === 0)) {
                return phoneError(409, 'not_configured', 'Add your number to the phone book before turning phone on.');
            }
            this.settings = merged;
            return { json: this.settings };
        }
        if (key === 'POST /phone/contacts') {
            this.pending = { purpose: 'add', number: body.number, label: body.label };
            this.simCalls.push({ sid: `c${this.simCalls.length}`, to: body.number, from: '+15550100000', script: '<Response><Say>This is GAIA. Your verification code is 1, 2, 3, 4, 5, 6.</Say><Hangup/></Response>' });
            const first = this.contacts[0];
            return { json: {
                verification_id: 'a'.repeat(32), number: `+1 •••• ${String(body.number).slice(-4)}`,
                needs_confirm_code: !!first, confirm_sent_to: first ? `+1 •••• ${first.number.slice(-4)}` : null, expires_in: 600,
            } };
        }
        if (key === 'POST /phone/contacts/confirm') {
            if (body.code !== '123456' || (this.contacts.length && body.confirm_code !== '654321')) {
                return phoneError(422, 'verification_failed', "That code isn't right. 2 tries left.", { tries_left: 2 });
            }
            const id = this.nextId++;
            this.contacts.push({ id, label: this.pending!.label ?? '', number: this.pending!.number! });
            this.pending = null;
            return { json: { id, label: '', number: '' } };
        }
        const remove = /^POST \/phone\/contacts\/(\d+)\/remove$/.exec(key);
        if (remove) {
            const c = this.contacts.find((x) => x.id === Number(remove[1]))!;
            this.pending = { purpose: 'remove', contactId: c.id };
            return { json: { verification_id: 'b'.repeat(32), number: `+1 •••• ${c.number.slice(-4)}` } };
        }
        if (key === 'POST /phone/contacts/remove/confirm') {
            if (body.code !== '111111') return phoneError(422, 'verification_failed', "That code isn't right. 2 tries left.", { tries_left: 2 });
            this.contacts = this.contacts.filter((x) => x.id !== this.pending!.contactId);
            return { json: this.status() };
        }
        if (key === 'POST /phone/test') {
            const alert: PhoneAlert = {
                id: 100 + this.alerts.length, source: 'test', kind: 'test', title: 'Test alert', reason: '', count: 1, state: 'open',
                urgent: true, created_at: 1_793_000_000, reached: false, held: '', deliveries: [],
            };
            this.alerts.unshift(alert);
            return { json: { alert } };
        }
        const handled = /^POST \/phone\/alerts\/(\d+)\/handled$/.exec(key);
        if (handled) {
            const a = this.alerts.find((x) => x.id === Number(handled[1]))!;
            a.state = 'handled';
            return { json: a };
        }
        if (key === 'POST /phone/keys') return phoneError(422, 'bad_credentials', 'Twilio rejected the Account SID or Auth Token.');
        return { status: 404, json: { detail: `no fake for ${key}` } };
    }

    install(): void {
        vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
            const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://x');
            const path = url.pathname.replace(/^\/api/, '') + url.search;
            const method = init?.method ?? 'GET';
            const body = init?.body ? JSON.parse(String(init.body)) : undefined;
            this.calls.push({ method, path, body });
            const reply = this.route(method, path, body ?? {});
            return new Response(JSON.stringify(reply.json), { status: reply.status ?? 200, headers: { 'content-type': 'application/json' } });
        }));
    }
}
