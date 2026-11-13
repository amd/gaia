// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Automations lists what GAIA runs on its own and pauses, resumes or removes it. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AutomationsSettings, daysPhrase, everyPhrase } from '../AutomationsSettings';
import { useChatStore } from '../../../stores/chatStore';
import { translator } from '../../../i18n/useT';
import type { Automation } from '../../../services/automationsApi';

const DIGEST: Automation = {
    id: 'a1b2c3d4', name: 'Inbox digest', kind: 'summary', heartbeat: false, paused: false,
    every_seconds: 86400, next_at: 1_900_000_000, last_run_at: 0, runs: 0,
    prompt: 'Summarize my inbox', say: '', check: '', quiet: '22:00-07:00', days: '', failures: 0,
};
const BEAT: Automation = {
    ...DIGEST, id: 'e5f6a7b8', name: 'Heartbeat', kind: 'alert', heartbeat: true, every_seconds: 1800,
    prompt: 'Check my calendar and CI', quiet: '',
};
const CI: Automation = {
    ...DIGEST, id: 'c1c1c1c1', name: 'Main CI', kind: 'trigger', every_seconds: 900,
    check: 'gaia automation ci-status --repo amd/gaia --branch main', failures: 3, quiet: '',
};

type Reply = { status?: number; body: unknown };
let routes: Record<string, () => Reply | Promise<Reply>>;
let calls: Array<{ key: string; headers: Record<string, string> }>;

beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const path = new URL(input, 'http://localhost').pathname.replace(/^\/api/, '');
        const key = `${init?.method ?? 'GET'} ${path}`;
        calls.push({ key, headers: (init?.headers ?? {}) as Record<string, string> });
        const handler = routes[key];
        if (!handler) throw new Error(`no route for ${key}`);
        const reply = await handler();
        return new Response(JSON.stringify(reply.body), {
            status: reply.status ?? 200,
            headers: { 'content-type': 'application/json' },
        });
    }));
});

afterEach(() => {
    vi.unstubAllGlobals();
    useChatStore.setState({ locale: 'en' });
});

describe('AutomationsSettings', () => {
    it('lists each automation with its kind and repeat', async () => {
        routes = { 'GET /automations': () => ({ body: { automations: [DIGEST, BEAT, CI], delivers: true } }) };
        render(<AutomationsSettings />);
        const digest = await screen.findByText('Inbox digest');
        const card = digest.closest('li')!;
        expect(within(card).getByText('Summary')).toBeTruthy();
        expect(within(card).getByText('Every day')).toBeTruthy();
        expect(within(card).getByText('22:00-07:00')).toBeTruthy();
        const beat = screen.getByText('Heartbeat', { selector: 'bdi' }).closest('li')!;
        expect(within(beat).getByText('Heartbeat', { selector: '.automation-kind' })).toBeTruthy();
        expect(within(beat).getByText('Every 30 minutes')).toBeTruthy();
        const ci = screen.getByText('Main CI').closest('li')!;
        expect(within(ci).getByText('Its check has failed 3 times in a row.')).toBeTruthy();
        expect(screen.queryByText(/Nothing delivers these yet/)).toBeNull();
    });

    it('says how to add one when there are none', async () => {
        routes = { 'GET /automations': () => ({ body: { automations: [], delivers: false } }) };
        render(<AutomationsSettings />);
        expect(await screen.findByText(/No automations yet/)).toBeTruthy();
        expect(screen.queryByText(/Nothing delivers these yet/)).toBeNull();
    });

    it('points at Messaging when nothing would deliver them', async () => {
        routes = { 'GET /automations': () => ({ body: { automations: [DIGEST], delivers: false } }) };
        render(<AutomationsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Open Messaging' }));
        expect(useChatStore.getState().settingsSection).toBe('messaging');
    });

    it('pauses with one click, no prompt, and shows the new state', async () => {
        let paused = false;
        routes = {
            'GET /automations': () => ({ body: { automations: [{ ...DIGEST, paused }], delivers: true } }),
            'POST /automations/a1b2c3d4/pause': () => { paused = true; return { body: { ...DIGEST, paused } }; },
        };
        render(<AutomationsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
        expect(await screen.findByRole('button', { name: 'Resume' })).toBeTruthy();
        expect(screen.getByText('Paused')).toBeTruthy();
        expect(screen.queryByRole('alertdialog')).toBeNull();
        const write = calls.find((c) => c.key === 'POST /automations/a1b2c3d4/pause')!;
        expect(write.headers['x-gaia-ui']).toBe('1');
    });

    it('resumes a paused one', async () => {
        routes = {
            'GET /automations': () => ({ body: { automations: [{ ...DIGEST, paused: true }], delivers: true } }),
            'POST /automations/a1b2c3d4/resume': () => ({ body: DIGEST }),
        };
        render(<AutomationsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Resume' }));
        await waitFor(() => expect(calls.some((c) => c.key === 'POST /automations/a1b2c3d4/resume')).toBe(true));
    });

    it('removes only after the confirmation, and cancel keeps it', async () => {
        let items = [DIGEST];
        routes = {
            'GET /automations': () => ({ body: { automations: items, delivers: true } }),
            'DELETE /automations/a1b2c3d4': () => { items = []; return { body: DIGEST }; },
        };
        render(<AutomationsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
        const sheet = screen.getByRole('alertdialog');
        fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }));
        expect(calls.some((c) => c.key.startsWith('DELETE'))).toBe(false);

        fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
        expect(await screen.findByText(/No automations yet/)).toBeTruthy();
    });

    it('shows the backend reason when a change fails', async () => {
        routes = {
            'GET /automations': () => ({ body: { automations: [DIGEST], delivers: true } }),
            'POST /automations/a1b2c3d4/pause': () => ({ status: 404, body: { detail: 'No automation is called a1b2c3d4.' } }),
        };
        render(<AutomationsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
        expect(await screen.findByText('No automation is called a1b2c3d4.')).toBeTruthy();
    });

    it('offers a retry when the list cannot load', async () => {
        routes = { 'GET /automations': () => ({ status: 500, body: { detail: 'automations.json is not usable' } }) };
        render(<AutomationsSettings />);
        expect(await screen.findByText(/automations.json is not usable/)).toBeTruthy();
        routes = { 'GET /automations': () => ({ body: { automations: [DIGEST], delivers: true } }) };
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('Inbox digest')).toBeTruthy();
    });
});

describe('AutomationsSettings under overlapping requests', () => {
    it('keeps a row disabled until its own change finishes', async () => {
        let finishDigest: (r: Reply) => void = () => {};
        routes = {
            'GET /automations': () => ({ body: { automations: [DIGEST, CI], delivers: true } }),
            'POST /automations/a1b2c3d4/pause': () => new Promise<Reply>((resolve) => { finishDigest = resolve; }),
            'POST /automations/c1c1c1c1/pause': () => ({ body: { ...CI, paused: true } }),
        };
        render(<AutomationsSettings />);
        const digest = (await screen.findByText('Inbox digest')).closest('li')!;
        const ci = screen.getByText('Main CI').closest('li')!;
        fireEvent.click(within(digest).getByRole('button', { name: 'Pause' }));
        fireEvent.click(within(ci).getByRole('button', { name: 'Pause' }));
        await waitFor(() => expect(calls.filter((c) => c.key === 'GET /automations').length).toBe(2));
        // The second row's change is done; the first is still in flight.
        const still = screen.getByText('Inbox digest').closest('li')!;
        expect((within(still).getByRole('button', { name: 'Pause' }) as HTMLButtonElement).disabled).toBe(true);
        finishDigest({ body: { ...DIGEST, paused: true } });
        await waitFor(() => expect(calls.filter((c) => c.key === 'GET /automations').length).toBe(3));
    });

    it('ignores a list reply that a newer request has overtaken', async () => {
        const replies: Array<(r: Reply) => void> = [];
        routes = { 'GET /automations': () => new Promise<Reply>((resolve) => { replies.push(resolve); }) };
        render(<AutomationsSettings />);
        await waitFor(() => expect(replies.length).toBe(1));
        replies[0]({ status: 500, body: { detail: 'first try failed' } });
        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(replies.length).toBe(3));
        replies[2]({ body: { automations: [DIGEST], delivers: true } });
        expect(await screen.findByText('Inbox digest')).toBeTruthy();
        replies[1]({ body: { automations: [], delivers: true } });
        await new Promise((r) => setTimeout(r, 20));
        expect(screen.getByText('Inbox digest')).toBeTruthy();
        expect(screen.queryByText(/No automations yet/)).toBeNull();
    });

    it('says it could not check Discord instead of claiming nothing delivers', async () => {
        routes = { 'GET /automations': () => ({ body: { automations: [DIGEST], delivers: null } }) };
        render(<AutomationsSettings />);
        expect(await screen.findByText(/couldn’t check whether Discord is connected/)).toBeTruthy();
        expect(screen.queryByText(/Nothing delivers these yet/)).toBeNull();
    });
});

describe('everyPhrase', () => {
    it('words the interval in the largest whole unit', () => {
        const t = translator('en');
        expect(everyPhrase(604800, t)).toBe('Every week');
        expect(everyPhrase(7200, t)).toBe('Every 2 hours');
        expect(everyPhrase(300, t)).toBe('Every 5 minutes');
    });
});

describe('daysPhrase', () => {
    it('words the stored days', () => {
        const t = translator('en');
        expect(daysPhrase('mon,tue,wed,thu,fri', t)).toBe('Weekdays');
        expect(daysPhrase('sat,sun', t)).toBe('Weekends');
        expect(daysPhrase('mon,wed', t)).toBe('Mon, Wed');
        expect(daysPhrase('someday', t)).toBe('someday');
    });
});
