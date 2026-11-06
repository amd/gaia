// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../../stores/chatStore';
import { PhoneSettings } from '../PhoneSettings';
import { talkStatus } from '../PhoneTalkSettings';
import { FakePhone } from './phoneFake';

let phone: FakePhone;

beforeEach(() => {
    phone = new FakePhone();
    phone.configured = true;
    phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }];
    phone.install();
    act(() => useChatStore.setState({ locale: 'en' }));
});
afterEach(() => vi.unstubAllGlobals());

const status = () => document.querySelector('.phone-talk-status');

describe('talkStatus', () => {
    it('is ready only with a PIN, talking on, and mobile access reaching this PC', () => {
        expect(talkStatus({ pin_set: true, talk_on_calls: true, talk_available: true })).toMatchObject({ state: 'ready' });
        expect(talkStatus({ pin_set: false, talk_on_calls: true, talk_available: false })).toEqual({ state: 'missing', needsPin: true, needsAccess: true });
        expect(talkStatus({ pin_set: true, talk_on_calls: false, talk_available: true })).toMatchObject({ state: 'off' });
    });
});

describe('Settings › Phone › Talk on calls', () => {
    it('says what is missing, then Ready once the PIN is set', async () => {
        phone.settings.talk_available = false;
        render(<PhoneSettings />);
        await screen.findByText(/After GAIA says what happened/);
        expect(status()).toHaveTextContent('Set a PIN.');
        expect(status()).toHaveTextContent('Turn on mobile access so calls can reach this PC.');

        phone.settings.talk_available = true;
        fireEvent.click(screen.getByRole('button', { name: 'Set PIN' }));
        const pin = screen.getByLabelText('New PIN');
        expect(pin).toHaveAttribute('type', 'password');
        fireEvent.change(pin, { target: { value: '12ab' } });
        expect(pin).toHaveValue('12');
        fireEvent.click(screen.getByRole('button', { name: 'Save PIN' }));
        expect(screen.getByText('A PIN is 4 to 8 digits.')).toBeInTheDocument();
        expect(phone.bodies('POST', '/phone/pin')).toEqual([]);

        fireEvent.change(pin, { target: { value: '482915' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save PIN' }));
        await screen.findByText('PIN saved.');
        expect(phone.bodies('POST', '/phone/pin')).toEqual([{ pin: '482915' }]);
        expect(screen.queryByLabelText('New PIN')).not.toBeInTheDocument();
        await waitFor(() => expect(status()).toHaveTextContent('Ready: calls can talk.'));
        expect(document.body.innerHTML).not.toContain('482915');
    });

    it('removes the PIN', async () => {
        phone.settings.pin_set = true;
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove PIN' }));
        await screen.findByText(/PIN removed/);
        expect(phone.calls.filter((c) => c.method === 'DELETE' && c.path === '/phone/pin')).toHaveLength(1);
        expect(status()).toHaveTextContent('Set a PIN.');
    });

    it('the toggle and the voice each send only their own key', async () => {
        phone.settings.pin_set = true;
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Talk with GAIA on calls' }));
        await waitFor(() => expect(phone.bodies('PUT', '/phone/settings')).toEqual([{ talk_on_calls: false }]));
        await waitFor(() => expect(status()).toHaveTextContent('Off: calls only say what happened.'));

        const voice = screen.getByLabelText('Voice on calls');
        await waitFor(() => expect(voice).toBeEnabled());
        fireEvent.change(voice, { target: { value: 'bm_george' } });
        await waitFor(() => expect(phone.bodies('PUT', '/phone/settings')[1]).toEqual({ voice: 'bm_george' }));
    });

    it('previews in the chosen voice through the read-aloud speech route', async () => {
        render(<PhoneSettings />);
        const play = vi.fn(async () => undefined);
        vi.stubGlobal('Audio', vi.fn(function (this: Record<string, unknown>, src: string) { Object.assign(this, { src, play, pause: vi.fn() }); }));
        URL.createObjectURL = vi.fn(() => 'blob:preview');
        URL.revokeObjectURL = vi.fn();
        const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
        const fallback = fetchMock.getMockImplementation() as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
        fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => (
            String(input).endsWith('/speech') ? new Response('RIFF', { status: 200, headers: { 'content-type': 'audio/wav' } }) : fallback(input, init)
        ));
        fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
        await waitFor(() => expect(play).toHaveBeenCalled());
        const speech = phone.calls.find((c) => c.path === '/speech');
        const sent = speech ? speech.body : JSON.parse(String(fetchMock.mock.calls.find(([u]) => String(u).endsWith('/speech'))![1]!.body));
        expect(sent).toMatchObject({ voice: 'af_nicole' });
    });
});
