// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemorySettings } from '../MemorySettings';
import * as memoryApi from '../../../services/memoryApi';

vi.mock('../../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);
const REMEMBER = 'Remember across chats';

function memory(enabled: boolean): memoryApi.MemorySettings {
    return { memory_enabled: enabled, mcp_memory_enabled: false, system_discovery_consent: false };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMemorySettings.mockResolvedValue(memory(true));
    mockedApi.updateMemorySettings.mockResolvedValue(memory(false));
});

describe('MemorySettings', () => {
    it('does not save until the current setting is known', () => {
        mockedApi.getMemorySettings.mockReturnValue(new Promise(() => {}));
        render(<MemorySettings />);
        const toggle = screen.getByRole('checkbox', { name: REMEMBER });
        expect(toggle).toBeDisabled();
        fireEvent.click(toggle);
        expect(mockedApi.updateMemorySettings).not.toHaveBeenCalled();
    });

    it('turns memory off through updateMemorySettings, once the save returns', async () => {
        let resolveSave: (value: memoryApi.MemorySettings) => void = () => {};
        mockedApi.updateMemorySettings.mockImplementation(
            () => new Promise((resolve) => { resolveSave = resolve; }),
        );
        render(<MemorySettings />);
        const toggle = await screen.findByRole('checkbox', { name: REMEMBER });
        await waitFor(() => expect(toggle).toBeEnabled());
        expect(toggle).toBeChecked();
        fireEvent.click(toggle);
        await waitFor(() => expect(mockedApi.updateMemorySettings).toHaveBeenCalledExactlyOnceWith({ memory_enabled: false }));
        expect(toggle).toBeChecked();
        expect(toggle).toBeDisabled();
        await act(async () => { resolveSave(memory(false)); });
        await waitFor(() => expect(toggle).not.toBeChecked());
        expect(toggle).toBeEnabled();
    });

    it('turns memory on through updateMemorySettings', async () => {
        mockedApi.getMemorySettings.mockResolvedValue(memory(false));
        mockedApi.updateMemorySettings.mockResolvedValue(memory(true));
        render(<MemorySettings />);
        const toggle = await screen.findByRole('checkbox', { name: REMEMBER });
        await waitFor(() => expect(toggle).toBeEnabled());
        expect(toggle).not.toBeChecked();
        fireEvent.click(toggle);
        await waitFor(() => expect(mockedApi.updateMemorySettings).toHaveBeenCalledExactlyOnceWith({ memory_enabled: true }));
        await waitFor(() => expect(toggle).toBeChecked());
    });

    it('keeps memory on and says why when the save fails', async () => {
        mockedApi.updateMemorySettings.mockRejectedValue(new Error('Memory service is down'));
        render(<MemorySettings />);
        const toggle = await screen.findByRole('checkbox', { name: REMEMBER });
        await waitFor(() => expect(toggle).toBeEnabled());
        fireEvent.click(toggle);
        expect(await screen.findByRole('alert')).toHaveTextContent('Memory service is down');
        expect(toggle).toBeChecked();
        expect(mockedApi.updateMemorySettings).toHaveBeenCalledExactlyOnceWith({ memory_enabled: false });
    });
});
