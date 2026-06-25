// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPage } from '../SettingsPage';
import { useChatStore } from '../../stores/chatStore';
import type { Settings } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

// Heavy child sections make their own API calls; stub them so this test
// stays focused on the Dynamic Tools toggle.
vi.mock('../CustomAgentsSection', () => ({ CustomAgentsSection: () => null }));
vi.mock('../ConnectorsSection', () => ({ ConnectorsSection: () => null }));

const mockedApi = vi.mocked(api);

function makeSettings(overrides: Partial<Settings> = {}): Settings {
    return {
        custom_model: null,
        model_status: null,
        context_size: null,
        dynamic_tools: false,
        dynamic_tools_locked: false,
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getSystemStatus.mockResolvedValue(null as never);
    mockedApi.getMCPRuntimeStatus.mockResolvedValue({ servers: [] } as never);
    mockedApi.getSettings.mockResolvedValue(makeSettings());
    mockedApi.updateSettings.mockImplementation(async (patch) =>
        makeSettings(patch as Partial<Settings>),
    );
    useChatStore.setState({ sessions: [], agents: [] });
});

function getDynamicToolsToggle(): HTMLInputElement {
    // Both enabled/disabled labels resolve to the same control.
    return (screen.queryByLabelText('Enable dynamic tools')
        ?? screen.getByLabelText('Disable dynamic tools')) as HTMLInputElement;
}

describe('SettingsPage — Dynamic Tools toggle (#1798)', () => {
    it('renders the loaded value (off by default)', async () => {
        render(<SettingsPage />);
        const toggle = await waitFor(getDynamicToolsToggle);
        expect(toggle).not.toBeChecked();
        expect(toggle).not.toBeDisabled();
    });

    it('reflects a persisted-on value from the server', async () => {
        mockedApi.getSettings.mockResolvedValue(makeSettings({ dynamic_tools: true }));
        render(<SettingsPage />);
        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
    });

    it('persists dynamic_tools: true when toggled on', async () => {
        render(<SettingsPage />);
        const toggle = await waitFor(getDynamicToolsToggle);

        await userEvent.click(toggle);

        await waitFor(() =>
            expect(mockedApi.updateSettings).toHaveBeenCalledWith({ dynamic_tools: true }),
        );
        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
    });

    it('is disabled and reflects the env value when locked', async () => {
        mockedApi.getSettings.mockResolvedValue(
            makeSettings({ dynamic_tools: true, dynamic_tools_locked: true }),
        );
        render(<SettingsPage />);
        const toggle = await waitFor(getDynamicToolsToggle);

        expect(toggle).toBeChecked();
        expect(toggle).toBeDisabled();
        expect(screen.getByText(/GAIA_DYNAMIC_TOOLS/)).toBeInTheDocument();
    });

    it('reverts and surfaces an error when the save fails (no silent fallback)', async () => {
        mockedApi.updateSettings.mockRejectedValue(new Error('network down'));
        render(<SettingsPage />);
        const toggle = await waitFor(getDynamicToolsToggle);

        await userEvent.click(toggle);

        await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
        expect(screen.getByText('network down')).toBeInTheDocument();
        // Optimistic flip reverted back to off.
        await waitFor(() => expect(getDynamicToolsToggle()).not.toBeChecked());
    });
});
