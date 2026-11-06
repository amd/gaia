// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../../stores/chatStore';
import { PhoneSettings } from '../PhoneSettings';
import { FakePhone, phoneError } from './phoneFake';

let phone: FakePhone;

beforeEach(() => {
    phone = new FakePhone();
    phone.install();
    act(() => useChatStore.setState({ locale: 'en' }));
});
afterEach(() => vi.unstubAllGlobals());

const putBodies = () => phone.bodies('PUT', '/phone/settings');

async function setUpSimulated() {
    render(<PhoneSettings />);
    await screen.findByText('How phone alerts work');
    expect(screen.getByRole('radio', { name: /Simulated phone/ })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Use the Simulated phone' }));
    await waitFor(() => expect(phone.bodies('POST', '/phone/simulated')).toHaveLength(1));
    await screen.findByText('Using the Simulated phone. Nothing is really sent.');
}

describe('Settings › Phone, first time', () => {
    it('explains what phone alerts are for, what leaves the PC and the cost', async () => {
        render(<PhoneSettings />);
        expect(await screen.findByText(/For things that need you now/)).toBeInTheDocument();
        expect(screen.getByText(/No file names, email, task output or agent text leave this PC/)).toBeInTheDocument();
        expect(screen.getByText(/\$1\.15.*a month/)).toBeInTheDocument();
    });

    it('simulated → add a number → code → turn on → test alert', async () => {
        await setUpSimulated();

        fireEvent.change(screen.getByLabelText('Number'), { target: { value: '+15551234567' } });
        fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'My phone' } });
        fireEvent.click(screen.getByRole('button', { name: /Send code/ }));
        await waitFor(() => expect(phone.bodies('POST', '/phone/contacts')).toEqual([{ number: '+15551234567', label: 'My phone', via: 'call' }]));

        // The Simulated phone shows the code it "called" with.
        expect(await screen.findByText(/Your verification code is 1, 2, 3, 4, 5, 6/)).toBeInTheDocument();
        expect(screen.getByText('With the Simulated phone, the code is in the Simulated phone log below.')).toBeInTheDocument();

        const codeBox = screen.getByLabelText(/Code sent to/);
        fireEvent.change(codeBox, { target: { value: '999999' } });
        fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
        expect(await screen.findByText("That code isn't right. 2 tries left.")).toBeInTheDocument();

        fireEvent.change(codeBox, { target: { value: '123456' } });
        fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
        await screen.findByText('Number added. Any numbers already here were told by phone.');
        expect(phone.bodies('POST', '/phone/contacts/confirm').slice(-1)[0]).toEqual({ verification_id: 'a'.repeat(32), code: '123456' });
        expect(screen.getByText('My phone')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('checkbox', { name: 'Phone alerts' }));
        await waitFor(() => expect(putBodies()).toEqual([{ enabled: true }]));
        await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Phone alerts' })).toBeChecked());

        fireEvent.click(screen.getByRole('button', { name: /Send test alert/ }));
        await screen.findByText('Test alert sent. It shows under Alerts.');
        expect(phone.bodies('POST', '/phone/test')).toHaveLength(1);
        expect(await screen.findAllByText('Test alert')).not.toHaveLength(0);
    });

    it('refuses to turn on before a number is in the phone book, in plain words', async () => {
        await setUpSimulated();
        fireEvent.click(screen.getByRole('checkbox', { name: 'Phone alerts' }));
        expect(await screen.findByText('Choose a provider and add your number first.')).toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: 'Phone alerts' })).not.toBeChecked();
    });

    it('a second number needs a second code, sent to the first one', async () => {
        phone.configured = true;
        phone.settings.from_number = '+15550100000';
        phone.contacts = [{ id: 7, label: 'Me', number: '+15550004321' }];
        render(<PhoneSettings />);
        fireEvent.change(await screen.findByLabelText('Number'), { target: { value: '+15551234567' } });
        fireEvent.click(screen.getByRole('button', { name: /Send code/ }));
        const second = await screen.findByLabelText(/the number already in the phone book/);
        expect(second.closest('.settings-field')).toHaveTextContent('4321');
        fireEvent.change(screen.getByLabelText(/Code sent to .*4567/), { target: { value: '123456' } });
        fireEvent.change(second, { target: { value: '654321' } });
        fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
        await waitFor(() => expect(phone.bodies('POST', '/phone/contacts/confirm').slice(-1)[0]).toEqual({
            verification_id: 'a'.repeat(32), code: '123456', confirm_code: '654321',
        }));
    });
});

describe('Settings › Phone, Twilio keys', () => {
    it('shows Twilio rejecting the keys in plain words, and forgets the token', async () => {
        render(<PhoneSettings />);
        await screen.findByText('How phone alerts work');
        fireEvent.click(screen.getByRole('radio', { name: /Twilio/ }));
        expect(screen.getByRole('note')).toHaveTextContent(/just for GAIA, keep its balance low/);
        expect(screen.getByText(/On a trial account/)).toBeInTheDocument();
        const token = screen.getByLabelText(/Auth Token/);
        expect(token).toHaveAttribute('type', 'password');
        fireEvent.change(screen.getByLabelText(/Account SID/), { target: { value: 'ACxxxx' } });
        fireEvent.change(token, { target: { value: 'secret-token' } });
        fireEvent.change(screen.getByLabelText('Twilio number'), { target: { value: '+15557654321' } });
        fireEvent.click(screen.getByRole('button', { name: 'Check and save' }));
        expect(await screen.findByText('Twilio rejected the Account SID or Auth Token. Copy both again from the Twilio console.')).toBeInTheDocument();
        expect(phone.bodies('POST', '/phone/keys')).toEqual([{ account_sid: 'ACxxxx', auth_token: 'secret-token', from_number: '+15557654321' }]);
        expect(token).toHaveValue('');
        expect(document.body.textContent).not.toContain('secret-token');
    });

    it('asks for all three fields before calling Twilio', async () => {
        render(<PhoneSettings />);
        await screen.findByText('How phone alerts work');
        fireEvent.click(screen.getByRole('radio', { name: /Twilio/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Check and save' }));
        expect(screen.getByText('Fill in all three: the Account SID, the Auth Token and your Twilio number.')).toBeInTheDocument();
        expect(phone.bodies('POST', '/phone/keys')).toEqual([]);
    });
});

describe('Settings › Phone, removing a number', () => {
    it('sends a code to the number and removes it once the code is typed', async () => {
        phone.configured = true;
        phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }, { id: 2, label: 'Partner', number: '+15559876543' }];
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove +1 •••• 6543' }));
        expect(await screen.findByText(/GAIA called .*6543.* with a code/)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText(/Code sent to/), { target: { value: '111111' } });
        fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
        await screen.findByText('Number removed. The numbers left were told by phone.');
        expect(phone.bodies('POST', '/phone/contacts/remove/confirm')).toEqual([{ verification_id: 'b'.repeat(32), code: '111111' }]);
        expect(screen.queryByText('Partner')).not.toBeInTheDocument();
    });

    it('an expired code says so and offers only Cancel', async () => {
        phone.configured = true;
        phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }];
        phone.once('POST /phone/contacts/remove/confirm', phoneError(410, 'verification_expired', 'The code expired. Start again.'));
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove +1 •••• 4567' }));
        fireEvent.change(await screen.findByLabelText(/Code sent to/), { target: { value: '111111' } });
        fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
        expect(await screen.findByText('The code expired. Start again.')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('too many code requests says when to try again', async () => {
        phone.configured = true;
        phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }];
        phone.once('POST /phone/contacts/1/remove', phoneError(429, 'rate_limited', 'Too many code requests. Try again in an hour.'));
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove +1 •••• 4567' }));
        expect(await screen.findByText('Too many code requests. Try again in an hour.')).toBeInTheDocument();
    });
});

describe('Settings › Phone, rules and limits', () => {
    beforeEach(() => {
        phone.configured = true;
        phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }];
    });

    it('sends only the key that changed', async () => {
        render(<PhoneSettings />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Steps for A scheduled job fails' }), { target: { value: 'notify' } });
        await waitFor(() => expect(putBodies()).toEqual([{ rules: { schedule_failed: { steps: 'notify' } } }]));

        fireEvent.click(screen.getAllByRole('checkbox', { name: 'Urgent' })[2]);
        await waitFor(() => expect(putBodies()[1]).toEqual({ rules: { gaia_down: { urgent: true } } }));

        const after = screen.getByLabelText('Alert after');
        fireEvent.change(after, { target: { value: '15' } });
        fireEvent.blur(after);
        await waitFor(() => expect(putBodies()[2]).toEqual({ rules: { approval_waiting: { after_minutes: 15 } } }));

        const calls = screen.getByLabelText('Calls a day');
        fireEvent.change(calls, { target: { value: '3' } });
        fireEvent.blur(calls);
        await waitFor(() => expect(putBodies()[3]).toEqual({ limits: { calls_per_day: 3 } }));

        fireEvent.click(screen.getByRole('checkbox', { name: 'Agents may alert me' }));
        await waitFor(() => expect(putBodies()[4]).toEqual({ rules: { agent: { on: true } } }));
    });

    it('does not send a value outside its range, or one that did not change', async () => {
        render(<PhoneSettings />);
        const calls = await screen.findByLabelText('Calls a day');
        fireEvent.change(calls, { target: { value: '99' } });
        fireEvent.blur(calls);
        fireEvent.change(calls, { target: { value: '6' } });
        fireEvent.blur(calls);
        await new Promise((r) => setTimeout(r, 50));
        expect(putBodies()).toEqual([]);
    });

    it('quiet hours start with a default window and save one end at a time', async () => {
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Quiet hours' }));
        await waitFor(() => expect(putBodies()).toEqual([{ quiet_hours: { start: '22:00', end: '07:00' } }]));
        const from = await screen.findByLabelText('From');
        fireEvent.change(from, { target: { value: '23:00' } });
        fireEvent.blur(from);
        await waitFor(() => expect(putBodies()[1]).toEqual({ quiet_hours: { start: '23:00', end: '07:00' } }));
    });

    it('over mobile access, a change says to make it on the PC', async () => {
        phone.once('PUT /phone/settings', { status: 403, json: { detail: "Change this on the PC running GAIA. It can't be changed over mobile access or from another computer." } });
        render(<PhoneSettings />);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Send texts' }));
        expect(await screen.findByText('Change this on the PC running GAIA.')).toBeInTheDocument();
    });

    it('says when the background service is not running instead of showing the page', async () => {
        phone.once('GET /phone/status', phoneError(503, 'daemon_unavailable', "GAIA's background service isn't running."));
        render(<PhoneSettings />);
        expect(await screen.findByText(/background service isn't running, so phone settings can't be reached/)).toBeInTheDocument();
    });
});

describe('Settings › Phone, alerts', () => {
    it('lists recent alerts with each call and text and how it ended', async () => {
        phone.configured = true;
        phone.contacts = [{ id: 1, label: 'Me', number: '+15551234567' }];
        phone.alerts = [{
            id: 5, source: 'schedule', kind: 'schedule_failed', title: 'Scheduled job failed', reason: 'Nightly backup exited with code 2',
            count: 2, state: 'open', urgent: false, created_at: 1_793_000_000, reached: false, held: 'call_limit',
            deliveries: [
                { channel: 'text', number: '+1 •••• 4567', state: 'done', outcome: 'delivered', reached: false, error: null, at: 1 },
                { channel: 'call', number: '+1 •••• 4567', state: 'done', outcome: 'machine_start', reached: false, error: null, at: 2 },
                { channel: 'call', number: '+1 •••• 4567', state: 'done', outcome: 'no-answer', reached: false, error: null, at: 3 },
                { channel: 'call', number: '+1 •••• 4567', state: 'done', outcome: 'human', reached: true, error: null, at: 4 },
            ],
        }];
        render(<PhoneSettings />);
        expect(await screen.findByText('Scheduled job failed')).toBeInTheDocument();
        expect(screen.getByText('Not called: daily limit reached')).toBeInTheDocument();
        for (const word of ['delivered', 'voicemail', 'no answer', 'answered']) expect(screen.getByText(word)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Mark handled' }));
        await waitFor(() => expect(phone.bodies('POST', '/phone/alerts/5/handled')).toHaveLength(1));
        expect(await screen.findByText('Marked handled. No more texts or calls for it.')).toBeInTheDocument();
    });
});
