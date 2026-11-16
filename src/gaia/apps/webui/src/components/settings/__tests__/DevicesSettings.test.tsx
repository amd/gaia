// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Phones and tablets: the pairing code never outlives its window, and the section stays hidden while pairing is off. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DevicesSettings } from '../DevicesSettings';
import { useDeviceStore } from '../../../stores/deviceStore';
import { pairingLink } from '../../../utils/pairingLink';
import type { DeviceAccessStatus, DevicePairingOffer } from '../../../services/devicesApi';

vi.mock('qrcode', () => ({ default: { toDataURL: async () => 'data:image/png;base64,AAAA' } }));

const STATUS: DeviceAccessStatus = {
    listening: true, port: 4243, error_code: null, error_detail: null, pairing_open: false, pairing_expires_at: null,
    idle_expiry_days: 90, pending_expiry_minutes: 10, failed_redemptions: [],
};
const OFFER: DevicePairingOffer = {
    v: 1, name: 'PC', pc_id: 'pc-1', origins: ['https://192.168.1.10:4243'], pin: 'a'.repeat(64),
    invite: 'invitation', exp: Math.floor(Date.now() / 1000) + 600,
};

let replies: Record<string, { status?: number; body: unknown }>;

beforeEach(() => {
    document.documentElement.style.setProperty('--text-primary', '#111111');
    document.documentElement.style.setProperty('--bg-primary', '#FFFFFF');
    useDeviceStore.setState({ available: null, status: null, devices: [], loadError: null });
    replies = {
        'GET /devices/status': { body: STATUS },
        'GET /devices': { body: { devices: [] } },
        'POST /devices/pairing': { body: OFFER },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const key = `${init?.method ?? 'GET'} ${new URL(input, 'http://localhost').pathname.replace(/^\/api/, '')}`;
        const reply = replies[key];
        if (!reply) throw new Error(`no route for ${key}`);
        return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200, headers: { 'content-type': 'application/json' } });
    }));
});

afterEach(() => vi.unstubAllGlobals());

describe('DevicesSettings', () => {
    it('clears the pairing code the moment the window closes, as it does on the first pairing', async () => {
        render(<DevicesSettings />);
        replies['GET /devices/status'] = { body: { ...STATUS, pairing_open: true } };
        fireEvent.click(await screen.findByRole('button', { name: 'Pair a phone or tablet' }));
        expect(await screen.findByAltText('Pairing code for the GAIA app')).toBeInTheDocument();

        // A device paired: the backend closed the window and the PC's event stream refreshed the state.
        replies['GET /devices/status'] = { body: STATUS };
        await act(() => useDeviceStore.getState().refresh());
        await waitFor(() => expect(screen.queryByAltText('Pairing code for the GAIA app')).not.toBeInTheDocument());
    });

    it('marks pairing unavailable on a 403, which hides the section and the indicator', async () => {
        replies['GET /devices/status'] = { status: 403, body: { detail: 'Device pairing is off.' } };
        await useDeviceStore.getState().refresh();
        expect(useDeviceStore.getState()).toMatchObject({ available: false, status: null, loadError: null });
    });

    it('copies the pairing link and says so, or shows why it could not', async () => {
        const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('denied'));
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
        render(<DevicesSettings />);
        replies['GET /devices/status'] = { body: { ...STATUS, pairing_open: true } };
        fireEvent.click(await screen.findByRole('button', { name: 'Pair a phone or tablet' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Copy pairing link' }));
        expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
        expect(writeText).toHaveBeenCalledWith(pairingLink(OFFER));

        fireEvent.click(screen.getByRole('button', { name: 'Copied' }));
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't copy the link");
    });
});
