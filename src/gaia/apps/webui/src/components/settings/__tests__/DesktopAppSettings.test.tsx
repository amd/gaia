// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DesktopAppSettings } from '../DesktopAppSettings';
import type { TrayConfig, TraySettings } from '../../../types/agent';

const DEFAULTS: TraySettings = { minimizeToTray: true, startMinimized: false, startOnLogin: false };

let saved: TraySettings;
const getConfig = vi.fn(async (): Promise<TrayConfig> => ({ tray: { ...saved } }));
const setConfig = vi.fn(async ({ tray }: { tray: Partial<TraySettings> }): Promise<TrayConfig> => {
    saved = { ...saved, ...tray };
    return { tray: { ...saved } };
});

function withDesktopApp() {
    Object.defineProperty(window, 'gaiaAPI', {
        configurable: true,
        value: { tray: { getConfig, setConfig, setActivity: vi.fn(), onCommand: vi.fn() } },
    });
}

beforeEach(() => {
    saved = { ...DEFAULTS };
    getConfig.mockClear();
    setConfig.mockClear();
    withDesktopApp();
});
afterEach(() => { delete window.gaiaAPI; });

const toggle = (name: RegExp) => screen.getByRole('switch', { name });

describe('DesktopAppSettings', () => {
    it('shows the three toggles with their saved values', async () => {
        render(<DesktopAppSettings />);
        await waitFor(() => expect(toggle(/keep running when the window closes/i)).toBeChecked());
        expect(toggle(/start gaia when you sign in/i)).not.toBeChecked();
        expect(toggle(/start in the tray/i)).not.toBeChecked();
        expect(screen.getByRole('group', { name: 'Desktop app' })).toBeInTheDocument();
    });

    it.each([
        [/keep running when the window closes/i, 'minimizeToTray', false],
        [/start gaia when you sign in/i, 'startOnLogin', true],
        [/start in the tray/i, 'startMinimized', true],
    ] as const)('%s saves only its own setting, right away', async (name, key, value) => {
        render(<DesktopAppSettings />);
        await waitFor(() => expect(toggle(name)).toBeEnabled());
        await userEvent.click(toggle(name));
        expect(setConfig).toHaveBeenCalledExactlyOnceWith({ tray: { [key]: value } });
        await waitFor(() => (value ? expect(toggle(name)).toBeChecked() : expect(toggle(name)).not.toBeChecked()));
    });

    it('keeps keyboard focus on a switch while it saves', async () => {
        let finish: (cfg: TrayConfig) => void = () => {};
        setConfig.mockImplementationOnce(() => new Promise((r) => { finish = r; }));
        render(<DesktopAppSettings />);
        await waitFor(() => expect(toggle(/sign in/i)).toBeEnabled());
        toggle(/sign in/i).focus();
        await userEvent.keyboard(' ');
        expect(toggle(/sign in/i)).toBeEnabled();
        expect(toggle(/sign in/i)).toHaveFocus();
        expect(toggle(/sign in/i)).toHaveAttribute('aria-busy', 'true');
        expect(toggle(/start in the tray/i)).toBeDisabled();
        await userEvent.keyboard(' ');
        expect(setConfig).toHaveBeenCalledTimes(1);
        finish({ tray: { ...DEFAULTS, startOnLogin: true } });
        await waitFor(() => expect(toggle(/sign in/i)).toBeChecked());
        expect(toggle(/start in the tray/i)).toBeEnabled();
    });

    it('shows why a save failed and keeps the old value', async () => {
        setConfig.mockRejectedValueOnce(new Error('Could not add GAIA to the apps that start at sign-in: access denied'));
        render(<DesktopAppSettings />);
        await waitFor(() => expect(toggle(/sign in/i)).toBeEnabled());
        await userEvent.click(toggle(/sign in/i));
        expect(await screen.findByRole('alert')).toHaveTextContent('access denied');
        expect(toggle(/sign in/i)).not.toBeChecked();
    });

    it('says when the settings could not be read', async () => {
        getConfig.mockRejectedValueOnce(new Error('ipc closed'));
        render(<DesktopAppSettings />);
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't read the desktop settings: ipc closed");
        expect(toggle(/sign in/i)).toBeDisabled();
    });

    it('each toggle is described by its hint', async () => {
        render(<DesktopAppSettings />);
        await waitFor(() => expect(toggle(/start in the tray/i)).toBeEnabled());
        expect(toggle(/start in the tray/i)).toHaveAccessibleDescription(/opens without its window/i);
    });

    it('is absent in a browser, where there is no tray', () => {
        delete window.gaiaAPI;
        const { container } = render(<DesktopAppSettings />);
        expect(container).toBeEmptyDOMElement();
    });
});
