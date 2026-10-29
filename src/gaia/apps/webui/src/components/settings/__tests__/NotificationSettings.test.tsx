// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationSettings } from '../NotificationSettings';
import { DEFAULT_ALERT_SETTINGS, useAlertSettings } from '../../../stores/notificationStore';

beforeEach(() => {
    localStorage.clear();
    useAlertSettings.setState(DEFAULT_ALERT_SETTINGS);
    window.gaiaAPI = {} as typeof window.gaiaAPI;
});

afterEach(() => { delete window.gaiaAPI; });

const group = (name: string) => screen.getByRole('radiogroup', { name });

describe('NotificationSettings', () => {
    it('turns each notification type off on its own', () => {
        render(<NotificationSettings />);
        fireEvent.click(within(group('Has a question')).getByRole('radio', { name: 'Off' }));
        expect(useAlertSettings.getState().modes).toEqual({
            needs_approval: 'banner', question: 'off', finished: 'banner',
        });
        fireEvent.click(within(group('Finished')).getByRole('radio', { name: 'Badge' }));
        expect(within(group('Finished')).getByRole('radio', { name: 'Badge' })).toHaveAttribute('aria-checked', 'true');
        expect(within(group('Needs your approval')).getByRole('radio', { name: 'Banner' })).toHaveAttribute('aria-checked', 'true');
    });

    it('sets sound, flash, keep-awake and the battery floor in the desktop app', () => {
        render(<NotificationSettings />);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Play a sound' }));
        fireEvent.click(screen.getByRole('checkbox', { name: "Flash the taskbar when GAIA isn't focused" }));
        fireEvent.change(screen.getByLabelText('On battery, let it sleep below'), { target: { value: '30' } });
        const s = useAlertSettings.getState();
        expect([s.sound, s.flash, s.batteryFloor]).toEqual([true, false, 30]);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Keep the PC awake while GAIA works' }));
        expect(useAlertSettings.getState().keepAwake).toBe(false);
        expect(screen.getByLabelText('On battery, let it sleep below')).toBeDisabled();
    });

    it('in a browser, asks for banner permission and says keep-awake needs the app', () => {
        delete window.gaiaAPI;
        const requestPermission = vi.fn().mockResolvedValue('granted');
        vi.stubGlobal('Notification', Object.assign(function Notification() {}, { permission: 'default', requestPermission }));
        render(<NotificationSettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Allow banners in this browser' }));
        expect(requestPermission).toHaveBeenCalled();
        expect(screen.getByText('Keep-awake needs the GAIA desktop app.')).toBeInTheDocument();
        vi.unstubAllGlobals();
    });
});
