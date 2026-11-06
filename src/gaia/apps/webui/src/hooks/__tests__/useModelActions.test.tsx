// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useModelActions } from '../useModelActions';
import { ConnectionBanner } from '../../components/ConnectionBanner';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { SystemStatus } from '../../types';

vi.mock('../../services/api', async (importActual) => ({
    ...(await importActual<typeof import('../../services/api')>()),
    downloadModel: vi.fn(),
    getSystemStatus: vi.fn(),
}));

const mockedApi = vi.mocked(api);
const UNKNOWN = "Cannot download 'user.not-real': GAIA doesn't know where to download it from.";

function rejectWith400(detail: string) {
    mockedApi.downloadModel.mockRejectedValueOnce(new api.ApiError(detail, 400, detail, null));
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ backendConnected: true, currentSessionId: null, messages: [] });
});

describe('useModelActions.downloadModel', () => {
    it("keeps the server's 400 detail and stops the spinner", async () => {
        rejectWith400(UNKNOWN);
        const { result } = renderHook(() => useModelActions('user.not-real'));
        await act(() => result.current.downloadModel(false));

        expect(result.current.downloadError).toBe(UNKNOWN);
        expect(result.current.isDownloadingModel).toBe(false);
    });

    it('clears the error when the next attempt starts', async () => {
        rejectWith400(UNKNOWN);
        mockedApi.downloadModel.mockResolvedValueOnce({ status: 'downloading', model: 'Gemma-4-E4B-it-GGUF' });
        const { result, unmount } = renderHook(() => useModelActions('Gemma-4-E4B-it-GGUF'));
        await act(() => result.current.downloadModel(false));
        await act(() => result.current.downloadModel(true));

        expect(result.current.downloadError).toBeNull();
        expect(result.current.isDownloadingModel).toBe(true);
        unmount();
    });

    it('drops the error when the default model changes', async () => {
        rejectWith400(UNKNOWN);
        const { result, rerender } = renderHook(({ name }) => useModelActions(name), {
            initialProps: { name: 'user.not-real' },
        });
        await act(() => result.current.downloadModel(false));
        expect(result.current.downloadError).toBe(UNKNOWN);

        rerender({ name: 'user.not-real' });
        expect(result.current.downloadError).toBe(UNKNOWN);

        rerender({ name: 'Gemma-4-E4B-it-GGUF' });
        expect(result.current.downloadError).toBeNull();
    });

    it('reports a failure that is not an ApiError too', async () => {
        mockedApi.downloadModel.mockRejectedValueOnce('socket closed');
        const { result } = renderHook(() => useModelActions());
        await act(() => result.current.downloadModel(false));

        expect(result.current.downloadError).toBe('socket closed');
    });
});

describe('the download banner Retry', () => {
    it("replaces the old failure with the server's reason the retry was refused", async () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true,
            default_model_name: 'amd.Some-Gateway',
            model_loaded: 'amd.Active-Model',
            download_progress: { state: 'error', model_name: 'amd.Some-Gateway', message: 'Pull failed earlier.' },
        } as unknown as SystemStatus });
        const gateway = "'amd.Some-Gateway' is hosted on a gateway, so there is nothing to download.";
        rejectWith400(gateway);
        render(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('Pull failed earlier.');

        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry' })); });

        expect(mockedApi.downloadModel).toHaveBeenCalledWith('amd.Some-Gateway', false, false);
        expect(screen.getByRole('alert')).toHaveTextContent(gateway);
        expect(screen.getByRole('alert')).not.toHaveTextContent('Pull failed earlier.');
    });
});
