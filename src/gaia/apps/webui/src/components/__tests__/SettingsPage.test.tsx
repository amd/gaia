// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPage } from '../SettingsPage';
import { useChatStore } from '../../stores/chatStore';
import type { Settings, SystemStatus } from '../../types';
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

function makeSystemStatus(): SystemStatus {
    return {
        lemonade_running: true,
        model_loaded: null,
        embedding_model_loaded: false,
        disk_space_gb: 100,
        memory_available_gb: 16,
        initialized: true,
        version: '0.0.0-test',
        lemonade_version: null,
        model_size_gb: null,
        model_device: null,
        model_context_size: null,
        model_labels: null,
        gpu_name: null,
        gpu_vram_gb: null,
        tokens_per_second: null,
        time_to_first_token: null,
        processor_name: null,
        device_supported: true,
        context_size_sufficient: true,
        model_downloaded: true,
        default_model_name: null,
        default_model_size_gb: null,
        lemonade_url: null,
        expected_model_loaded: true,
        download_progress: null,
    };
}

// Resolve on a macrotask, like a real HTTP response — an immediately-resolved
// mock can flush before the first waitFor() check and mask load-order races.
function respondWith<T>(value: T): Promise<T> {
    return new Promise((resolve) => setTimeout(() => resolve(value), 30));
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getSystemStatus.mockImplementation(() => respondWith(makeSystemStatus()));
    mockedApi.getMCPRuntimeStatus.mockResolvedValue({ servers: [] } as never);
    mockedApi.getSettings.mockImplementation(() => respondWith(makeSettings()));
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

// The toggle exists (disabled, unchecked) from first paint; waiting for mere
// presence races the async getSettings() load. Wait for the settled state:
// the toggle stays disabled until settingsLoaded flips true.
async function findLoadedDynamicToolsToggle(): Promise<HTMLInputElement> {
    return waitFor(() => {
        const toggle = getDynamicToolsToggle();
        expect(toggle).not.toBeDisabled();
        return toggle;
    });
}

describe('SettingsPage — Dynamic Tools toggle (#1798)', () => {
    it('renders the loaded value (off by default)', async () => {
        render(<SettingsPage />);
        const toggle = await findLoadedDynamicToolsToggle();
        expect(toggle).not.toBeChecked();
        expect(toggle).not.toBeDisabled();
    });

    it('reflects a persisted-on value from the server', async () => {
        mockedApi.getSettings.mockImplementation(() =>
            respondWith(makeSettings({ dynamic_tools: true })),
        );
        render(<SettingsPage />);
        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
    });

    it('persists dynamic_tools: true when toggled on', async () => {
        render(<SettingsPage />);
        const toggle = await findLoadedDynamicToolsToggle();

        await userEvent.click(toggle);

        await waitFor(() =>
            expect(mockedApi.updateSettings).toHaveBeenCalledWith({ dynamic_tools: true }),
        );
        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
    });

    it('toggles when the visible row/label is clicked, not just the hidden input', async () => {
        // Regression: the checkbox is visually hidden (width:0/height:0) and the
        // track is a sibling <span>. Clicking the visible control only works if a
        // <label> forwards the click to the input. Clicking the input element
        // directly (as the other tests do) would pass even if that wiring is
        // missing — so click the visible label text here, the way a user does.
        render(<SettingsPage />);
        await findLoadedDynamicToolsToggle();

        await userEvent.click(screen.getByText('Enable dynamic tool loading'));

        await waitFor(() =>
            expect(mockedApi.updateSettings).toHaveBeenCalledWith({ dynamic_tools: true }),
        );
        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
    });

    it('is disabled and reflects the env value when locked', async () => {
        mockedApi.getSettings.mockImplementation(() =>
            respondWith(makeSettings({ dynamic_tools: true, dynamic_tools_locked: true })),
        );
        render(<SettingsPage />);
        // Locked keeps the toggle disabled even after load, so the enabled-state
        // wait can't apply here; the lock note only renders once settings land.
        await screen.findByText(/GAIA_DYNAMIC_TOOLS/);

        const toggle = getDynamicToolsToggle();
        expect(toggle).toBeChecked();
        expect(toggle).toBeDisabled();
    });

    it('guards against a double-click while a save is in flight (single PUT)', async () => {
        // Hold the first save open so the toggle stays mid-save between clicks.
        let resolveSave!: (value: Settings) => void;
        mockedApi.updateSettings.mockImplementationOnce(
            () => new Promise<Settings>((resolve) => { resolveSave = resolve; }),
        );

        render(<SettingsPage />);
        const toggle = await findLoadedDynamicToolsToggle();

        // First click starts the save; the toggle disables (savingDynamicTools).
        await userEvent.click(toggle);
        await waitFor(() => expect(getDynamicToolsToggle()).toBeDisabled());

        // Second click while the save is pending must be a no-op, not a second PUT.
        await userEvent.click(getDynamicToolsToggle());

        // Let the in-flight save settle.
        resolveSave(makeSettings({ dynamic_tools: true }));

        await waitFor(() => expect(getDynamicToolsToggle()).toBeChecked());
        expect(mockedApi.updateSettings).toHaveBeenCalledTimes(1);
    });

    it('reverts and surfaces an error when the save fails (no silent fallback)', async () => {
        mockedApi.updateSettings.mockRejectedValue(new Error('network down'));
        render(<SettingsPage />);
        const toggle = await findLoadedDynamicToolsToggle();

        await userEvent.click(toggle);

        await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
        expect(screen.getByText('network down')).toBeInTheDocument();
        // Optimistic flip reverted back to off.
        await waitFor(() => expect(getDynamicToolsToggle()).not.toBeChecked());
    });
});
