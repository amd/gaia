// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Messaging walks a person from no bot to a paired Discord DM. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MessagingSettings, formatPairingCode } from '../MessagingSettings';
import { useChatStore } from '../../../stores/chatStore';
import type { DiscordStatus } from '../../../services/messagingApi';

const BASE: DiscordStatus = {
    connected: false, paired: false, running: false, state: 'stopped', detail: '',
    bot_name: '', owner_name: '', autostart: false, always_allowed: [], model: null,
    portal_url: 'https://discord.com/developers/applications',
    bot_page_url: '', invite_url: '', bot_profile_url: '',
    pairing_code: '', pairing_expires_at: 0, discord_installed: true, should_offer_setup: true,
};

const BOT = {
    connected: true, bot_name: 'GAIA', running: true, autostart: true,
    bot_page_url: 'https://discord.com/developers/applications/111/bot',
    invite_url: 'https://discord.com/oauth2/authorize?client_id=111&scope=bot&permissions=0',
    bot_profile_url: 'https://discord.com/users/222',
};
const PAIRING: DiscordStatus = { ...BASE, ...BOT, state: 'pairing', pairing_code: 'ABCD2345', pairing_expires_at: 1e10 };
const ANSWERING: DiscordStatus = { ...BASE, ...BOT, state: 'connected', paired: true, owner_name: 'kalin' };

type Reply = { status?: number; body: unknown };
type Handler = (body: Record<string, unknown> | undefined) => Reply | Promise<Reply>;

let routes: Record<string, Handler>;
let calls: Array<{ key: string; body: Record<string, unknown> | undefined; headers: Record<string, string> }>;

function serve(table: Record<string, Handler>) {
    routes = table;
}

beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const path = new URL(input, 'http://localhost').pathname.replace(/^\/api/, '');
        const key = `${init?.method ?? 'GET'} ${path}`;
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ key, body, headers: (init?.headers ?? {}) as Record<string, string> });
        const handler = routes[key];
        if (!handler) throw new Error(`no route for ${key}`);
        const reply = await handler(body);
        return new Response(JSON.stringify(reply.body), {
            status: reply.status ?? 200,
            headers: { 'content-type': 'application/json' },
        });
    }));
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    useChatStore.setState({ locale: 'en' });
});

const ok = (body: unknown): Handler => () => ({ body });
const link = (name: string) => screen.getByRole('link', { name: new RegExp(name) });

describe('formatPairingCode', () => {
    it('splits an eight-character code in two', () => {
        expect(formatPairingCode('ABCD2345')).toBe('ABCD-2345');
        expect(formatPairingCode('ABC')).toBe('ABC');
    });
});

describe('MessagingSettings', () => {
    it('not connected: pitches, opens the Developer Portal and asks for the token', async () => {
        serve({ 'GET /messaging/discord': ok(BASE) });
        render(<MessagingSettings />);
        expect(await screen.findByText(/Message GAIA from Discord on your phone/)).toBeInTheDocument();
        expect(screen.getByText(/Only you can use it/)).toBeInTheDocument();
        expect(link('Open Discord Developer Portal')).toHaveAttribute('href', BASE.portal_url);
        expect(link('Open Discord Developer Portal')).toHaveAttribute('target', '_blank');
        expect(screen.getByText('Click “New Application”.')).toBeInTheDocument();
        expect(screen.getByLabelText('Bot token')).toHaveAttribute('type', 'password');
        expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
        expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
    });

    it('pairing: shows the formatted code, the invite page and the bot', async () => {
        serve({ 'GET /messaging/discord': ok(PAIRING) });
        render(<MessagingSettings />);
        expect(await screen.findByTestId('discord-pairing-code')).toHaveTextContent('ABCD-2345');
        expect(screen.getByText('Add the bot to a private server')).toBeInTheDocument();
        expect(link('Open invite page')).toHaveAttribute('href', PAIRING.invite_url);
        expect(link('Open the bot in Discord')).toHaveAttribute('href', PAIRING.bot_profile_url);
        expect(screen.getByText(/only person GAIA will answer/)).toBeInTheDocument();
        expect(screen.queryByLabelText('Bot token')).not.toBeInTheDocument();
    });

    it('pairing: copies the code', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
        serve({ 'GET /messaging/discord': ok(PAIRING) });
        render(<MessagingSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Copy code' }));
        expect(writeText).toHaveBeenCalledWith('ABCD2345');
        expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
    });

    it('pairing: watches the bridge and shows success once the code is sent', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        let current = PAIRING;
        serve({ 'GET /messaging/discord': () => ({ body: current }) });
        render(<MessagingSettings />);
        await screen.findByTestId('discord-pairing-code');

        current = ANSWERING;
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

        expect(await screen.findByText('Paired. GAIA answers you on Discord now.')).toBeInTheDocument();
        expect(screen.getByText(/Answering .*kalin.* as .*GAIA/)).toBeInTheDocument();
    });

    it('connected: answers the owner, with stop, re-pair and a confirmed disconnect', async () => {
        serve({
            'GET /messaging/discord': ok(ANSWERING),
            'POST /messaging/discord/disconnect': ok(BASE),
        });
        render(<MessagingSettings />);
        expect(await screen.findByText(/Answering .*kalin.* as .*GAIA/)).toBeInTheDocument();
        expect(link('Open in Discord')).toHaveAttribute('href', ANSWERING.bot_profile_url);
        expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Pair a different account' })).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        expect(calls.some((c) => c.key.includes('disconnect'))).toBe(false);
        const sheet = screen.getByRole('alertdialog', { name: 'Disconnect Discord?' });
        fireEvent.click(sheet.querySelector('.btn-danger') as HTMLElement);

        expect(await screen.findByLabelText('Bot token')).toBeInTheDocument();
        const post = calls.find((c) => c.key === 'POST /messaging/discord/disconnect');
        expect(post?.headers['x-gaia-ui']).toBe('1');
    });

    it('taken over: one button answers from this PC', async () => {
        serve({
            'GET /messaging/discord': ok({ ...ANSWERING, state: 'taken_over', detail: 'Another PC is answering this Discord bot.' }),
            'POST /messaging/discord/start': (body) => ({ body: body?.take_over ? ANSWERING : PAIRING }),
        });
        render(<MessagingSettings />);
        expect(await screen.findByText(/Another PC is answering this bot/)).toBeInTheDocument();
        expect(screen.queryByText('Another PC is answering this Discord bot.')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Answer from this PC' }));
        expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument();
        expect(calls.find((c) => c.key === 'POST /messaging/discord/start')?.body).toEqual({ take_over: true });
    });

    it('connected: lists what is always allowed and forgets it on request', async () => {
        let allowed = ['dir', 'write_file in Documents'];
        const forgot = vi.fn(() => { allowed = []; return { body: { ...ANSWERING, always_allowed: allowed } }; });
        serve({
            'GET /messaging/discord': () => ({ body: { ...ANSWERING, always_allowed: allowed } }),
            'POST /messaging/discord/forget-always': forgot,
        });
        render(<MessagingSettings />);
        expect(await screen.findByText('write_file in Documents')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Restart' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Ask me again for all of these' }));
        await waitFor(() => expect(screen.queryByText('write_file in Documents')).not.toBeInTheDocument());
        expect(forgot).toHaveBeenCalled();
    });

    it('stopped unexpectedly: says so and offers Start', async () => {
        serve({ 'GET /messaging/discord': ok({ ...ANSWERING, state: 'stopped', running: false }) });
        render(<MessagingSettings />);
        expect(await screen.findByText(/stopped answering on Discord unexpectedly/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    });

    it('stopped: offers Start', async () => {
        serve({ 'GET /messaging/discord': ok({ ...ANSWERING, state: 'stopped', running: false, autostart: false }) });
        render(<MessagingSettings />);
        expect(await screen.findByText("GAIA isn't answering on Discord right now.")).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    });

    it('start: says it is starting while the bridge comes up, not that it is off', async () => {
        serve({
            'GET /messaging/discord': ok({ ...ANSWERING, state: 'stopped', running: false }),
            'POST /messaging/discord/start': () => new Promise<Reply>(() => {}),
        });
        render(<MessagingSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Start' }));
        expect(await screen.findByText(/Connecting to Discord/)).toBeInTheDocument();
        expect(screen.queryByText("GAIA isn't answering on Discord right now.")).not.toBeInTheDocument();
    });

    it('error: shows the reason the bridge gave under the card sentence', async () => {
        serve({ 'GET /messaging/discord': ok({ ...ANSWERING, state: 'error', detail: 'The agent could not start: no model loaded.' }) });
        render(<MessagingSettings />);
        expect(await screen.findByText('The Discord bot stopped with an error.')).toBeInTheDocument();
        expect(screen.getByText('The agent could not start: no model loaded.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    });

    it('token invalid: shows the token step again with the reason', async () => {
        serve({ 'GET /messaging/discord': ok({ ...ANSWERING, state: 'token_invalid', detail: 'Discord rejected the token (401).' }) });
        render(<MessagingSettings />);
        expect(await screen.findByText(/Discord no longer accepts this token/)).toBeInTheDocument();
        expect(screen.getByLabelText('Bot token')).toBeInTheDocument();
    });

    it('connect: shows what Discord said when it refuses the token', async () => {
        serve({
            'GET /messaging/discord': ok(BASE),
            'POST /messaging/discord/connect': () => ({ status: 400, body: { detail: 'Discord rejected that token. Reset it and paste the new one.' } }),
        });
        render(<MessagingSettings />);
        fireEvent.change(await screen.findByLabelText('Bot token'), { target: { value: '  bad.token  ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Discord rejected that token. Reset it and paste the new one.');
        expect(calls.find((c) => c.key === 'POST /messaging/discord/connect')?.body).toEqual({ token: 'bad.token' });
    });

    it('connect: moves on to pairing and warns about a public bot', async () => {
        serve({
            'GET /messaging/discord': ok(BASE),
            'POST /messaging/discord/connect': ok({
                connect: { connected: true, bot_name: 'GAIA', application_id: '111', invite_url: PAIRING.invite_url, bot_page_url: PAIRING.bot_page_url, bot_public: true },
                status: PAIRING,
            }),
        });
        render(<MessagingSettings />);
        fireEvent.change(await screen.findByLabelText('Bot token'), { target: { value: 'good.token' } });
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        expect(await screen.findByTestId('discord-pairing-code')).toHaveTextContent('ABCD-2345');
        expect(screen.getByText(/turn off .*Public Bot/)).toBeInTheDocument();
        expect(link('Open the bot page')).toHaveAttribute('href', PAIRING.bot_page_url);
    });

    it('shows a status that failed to load, with a retry', async () => {
        serve({ 'GET /messaging/discord': () => ({ status: 403, body: { detail: 'Only this PC can change Discord.' } }) });
        render(<MessagingSettings />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Only this PC can change Discord.');
        serve({ 'GET /messaging/discord': ok(BASE) });
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(screen.getByLabelText('Bot token')).toBeInTheDocument());
    });

    it('renders in Arabic with the code kept left to right', async () => {
        useChatStore.setState({ locale: 'ar' });
        serve({ 'GET /messaging/discord': ok(PAIRING) });
        render(<MessagingSettings />);
        const code = await screen.findByTestId('discord-pairing-code');
        expect(code).toHaveAttribute('dir', 'ltr');
        expect(screen.getByText(/أضف البوت إلى خادم خاص/)).toBeInTheDocument();
    });
});
