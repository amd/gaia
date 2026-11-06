// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { HOME_ASSISTANT_DOCS, SmartHomeSettings } from '../SmartHomeSettings';

vi.mock('../../../services/api');

const mocked = vi.mocked(api);
const URL = 'http://homeassistant.local:8123';
const NOT_CONNECTED = { connected: false, url: null, active: false };
const CONNECTED = { connected: true, url: URL, active: true, enabled: true };

function fill(url: string, token: string) {
    fireEvent.change(screen.getByLabelText('Address'), { target: { value: url } });
    fireEvent.change(screen.getByLabelText('Access token'), { target: { value: token } });
}

const connectButton = () => screen.getByRole('button', { name: /Connect/ });

beforeEach(() => {
    vi.clearAllMocks();
});

describe('SmartHomeSettings', () => {
    it('not connected: shows the setup steps, a learn-more link and an empty form', async () => {
        mocked.getHomeAssistant.mockResolvedValue(NOT_CONNECTED);
        render(<SmartHomeSettings />);

        expect(await screen.findByText(/add the Model Context Protocol Server integration/)).toBeInTheDocument();
        expect(screen.getByText(/Settings › Voice assistants › Expose/)).toBeInTheDocument();
        expect(screen.getByText(/long-lived access token/)).toBeInTheDocument();
        expect(screen.getByText(/GAIA asks before every change to a device/)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: /Learn more/ })).toHaveAttribute('href', HOME_ASSISTANT_DOCS);
        expect(screen.getByLabelText('Address')).toHaveAttribute('placeholder', URL);
        expect(screen.getByLabelText('Access token')).toHaveAttribute('type', 'password');
        expect(connectButton()).toBeDisabled();
    });

    it('connecting: disables the button and shows a spinner, then the connected state', async () => {
        mocked.getHomeAssistant.mockResolvedValue(NOT_CONNECTED);
        let finish!: (v: Awaited<ReturnType<typeof api.connectHomeAssistant>>) => void;
        mocked.connectHomeAssistant.mockReturnValue(new Promise((r) => { finish = r; }));
        render(<SmartHomeSettings />);
        await screen.findByLabelText('Address');

        fill(` ${URL} `, 'secret-token');
        fireEvent.click(connectButton());

        const busy = screen.getByRole('button', { name: /Connecting…/ });
        expect(busy).toBeDisabled();
        expect(busy.querySelector('.spin')).not.toBeNull();
        expect(mocked.connectHomeAssistant).toHaveBeenCalledWith(URL, 'secret-token');

        finish({ ...CONNECTED, tool_count: 28, detail: 'Connected: 28 Home Assistant tools.' });
        expect(await screen.findByText('Home Assistant connected')).toBeInTheDocument();
        expect(screen.getByText('28 tools')).toBeInTheDocument();
        expect(screen.getByText(URL)).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Connected: 28 Home Assistant tools.');
        expect(screen.queryByLabelText('Access token')).toBeNull();
    });

    it('failed: shows the backend detail verbatim and keeps what was typed', async () => {
        mocked.getHomeAssistant.mockResolvedValue(NOT_CONNECTED);
        const detail = 'Home Assistant rejected this token. Create a new long-lived access token in your profile (Security tab) and paste it here.';
        mocked.connectHomeAssistant.mockRejectedValue(new Error(detail));
        render(<SmartHomeSettings />);
        await screen.findByLabelText('Address');

        fill(URL, 'bad-token');
        fireEvent.click(connectButton());

        expect(await screen.findByRole('alert')).toHaveTextContent(detail);
        expect(screen.getByLabelText('Address')).toHaveValue(URL);
        expect(screen.getByLabelText('Access token')).toHaveValue('bad-token');
        expect(connectButton()).toBeEnabled();
        expect(screen.queryByText('Home Assistant connected')).toBeNull();
    });

    it('connected: tests the connection and shows its result', async () => {
        mocked.getHomeAssistant.mockResolvedValue(CONNECTED);
        mocked.testHomeAssistant.mockResolvedValue({ ok: true, detail: 'Connected: 12 Home Assistant tools.', tool_count: 12 });
        render(<SmartHomeSettings />);

        expect(await screen.findByText('Home Assistant connected')).toBeInTheDocument();
        expect(screen.getByText(URL)).toBeInTheDocument();
        // No count until a connect or test reports one.
        expect(screen.queryByText(/\d+ tools?$/)).toBeNull();

        fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
        expect(await screen.findByRole('status')).toHaveTextContent('Connected: 12 Home Assistant tools.');
        expect(screen.getByText('12 tools')).toBeInTheDocument();
    });

    it('connected: a failed test shows its detail as an error', async () => {
        mocked.getHomeAssistant.mockResolvedValue(CONNECTED);
        mocked.testHomeAssistant.mockResolvedValue({ ok: false, detail: 'Home Assistant at http://homeassistant.local:8123 is not answering.' });
        render(<SmartHomeSettings />);

        fireEvent.click(await screen.findByRole('button', { name: 'Test connection' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Home Assistant at http://homeassistant.local:8123 is not answering.');
    });

    it('disconnect asks first, then shows the backend detail and the setup form', async () => {
        mocked.getHomeAssistant.mockResolvedValue(CONNECTED);
        const detail = 'GAIA forgot this Home Assistant. The token still works until you delete it in your Home Assistant profile (Security tab).';
        mocked.disconnectHomeAssistant.mockResolvedValue({ ...NOT_CONNECTED, detail });
        render(<SmartHomeSettings />);

        fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }));
        expect(screen.getByText(/GAIA will stop seeing and controlling your devices/)).toBeInTheDocument();
        expect(mocked.disconnectHomeAssistant).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByText(/GAIA will stop seeing/)).toBeNull();

        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        fireEvent.click(screen.getByRole('button', { name: /Disconnect/ }));
        expect(await screen.findByRole('status')).toHaveTextContent(detail);
        expect(mocked.disconnectHomeAssistant).toHaveBeenCalledTimes(1);
        expect(screen.getByText('Before you connect')).toBeInTheDocument();
        expect(screen.getByLabelText('Access token')).toHaveValue('');
    });

    it('saved but not in use for GAIA: says so and offers the token form', async () => {
        mocked.getHomeAssistant.mockResolvedValue({ ...CONNECTED, active: false });
        render(<SmartHomeSettings />);

        expect(await screen.findByText(/GAIA isn't using this Home Assistant yet/)).toBeInTheDocument();
        expect(screen.getByLabelText('Address')).toHaveValue(URL);
        expect(screen.getByLabelText('Access token')).toBeInTheDocument();
    });

    it('a status that fails to load says why and retries', async () => {
        mocked.getHomeAssistant.mockRejectedValueOnce(new Error('Service unavailable. Is the backend running?'));
        mocked.getHomeAssistant.mockResolvedValueOnce(NOT_CONNECTED);
        render(<SmartHomeSettings />);

        expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable. Is the backend running?');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(screen.getByText('Before you connect')).toBeInTheDocument());
    });
});
