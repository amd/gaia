// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PhoneAlert } from '../../services/phoneApi';
import { useChatStore } from '../../stores/chatStore';
import { PhoneAlertBanner } from '../PhoneAlertBanner';
import { FakePhone, phoneError } from '../settings/__tests__/phoneFake';

const alert = (id: number, kind: string): PhoneAlert => ({
    id, source: kind, kind, title: '', reason: '', count: 1, state: 'open', urgent: false, created_at: 1_793_000_000,
    reached: false, held: '', deliveries: [],
});

let phone: FakePhone;

beforeEach(() => {
    phone = new FakePhone();
    phone.install();
    act(() => useChatStore.setState({ locale: 'en', settingsSection: null }));
});
afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
});

describe('PhoneAlertBanner', () => {
    it('shows the newest open alert and how many more, and Open goes to Settings › Phone', async () => {
        phone.settings.enabled = true;
        phone.alerts = [alert(2, 'schedule_failed'), alert(1, 'approval_waiting')];
        render(<PhoneAlertBanner />);
        const banner = await screen.findByRole('alert');
        expect(banner).toHaveTextContent('Scheduled job failed');
        expect(banner).toHaveTextContent('(+1 more)');
        fireEvent.click(screen.getByRole('button', { name: 'Open' }));
        expect(useChatStore.getState().settingsSection).toBe('phone');
    });

    it('Dismiss marks every open alert handled, which stops their calls', async () => {
        phone.settings.enabled = true;
        phone.alerts = [alert(2, 'schedule_failed'), alert(1, 'approval_waiting')];
        render(<PhoneAlertBanner />);
        fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));
        await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
        expect(phone.bodies('POST', '/phone/alerts/2/handled')).toHaveLength(1);
        expect(phone.bodies('POST', '/phone/alerts/1/handled')).toHaveLength(1);
    });

    it('stays silent while phone is off, and does not ask for alerts', async () => {
        phone.alerts = [alert(2, 'schedule_failed')];
        render(<PhoneAlertBanner />);
        await waitFor(() => expect(phone.calls.some((c) => c.path === '/phone/status')).toBe(true));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(phone.calls.some((c) => c.path.startsWith('/phone/alerts'))).toBe(false);
    });

    it('stays silent, with no error, when the background service is down or outdated', async () => {
        const errors = vi.spyOn(console, 'error');
        phone.once('GET /phone/status', phoneError(503, 'daemon_unavailable', "GAIA's background service isn't running."));
        const { unmount } = render(<PhoneAlertBanner />);
        await waitFor(() => expect(phone.calls).toHaveLength(1));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        unmount();
        phone.once('GET /phone/status', phoneError(503, 'daemon_outdated', 'older version'));
        render(<PhoneAlertBanner />);
        await waitFor(() => expect(phone.calls).toHaveLength(2));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(errors.mock.calls.filter((c) => String(c[0]).includes('phone'))).toEqual([]);
        errors.mockRestore();
    });

    it('polls about every 15 seconds, and not while the app is hidden', async () => {
        vi.useFakeTimers();
        phone.settings.enabled = true;
        render(<PhoneAlertBanner />);
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });
        const statusCalls = () => phone.calls.filter((c) => c.path === '/phone/status').length;
        expect(statusCalls()).toBe(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
        expect(statusCalls()).toBe(2);
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
        try {
            await act(async () => { await vi.advanceTimersByTimeAsync(45_000); });
            expect(statusCalls()).toBe(2);
        } finally {
            Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
        }
    });
});
