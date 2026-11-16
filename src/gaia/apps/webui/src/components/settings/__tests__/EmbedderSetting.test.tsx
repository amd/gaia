// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EmbedderSetting } from '../EmbedderSetting';
import * as memoryApi from '../../../services/memoryApi';

vi.mock('../../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

function view(device: memoryApi.EmbedderDevice, chosen: boolean, npuAvailable = true): memoryApi.EmbedderView {
    return {
        device,
        chosen,
        profile_device: 'npu',
        options: [
            { device: 'gpu', model: 'user.embeddinggemma-300m-GGUF', display_name: 'EmbeddingGemma 300M', available: true, reason: null, downloaded: true },
            { device: 'npu', model: 'embed-gemma-300m-FLM', display_name: 'EmbeddingGemma 300M NPU', available: npuAvailable, reason: npuAvailable ? null : 'no NPU', downloaded: true },
        ],
    };
}

beforeEach(() => {
    vi.clearAllMocks();
});

describe('EmbedderSetting', () => {
    it('says which embedder runs and on which device', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('npu', false));
        render(<EmbedderSetting />);
        expect(await screen.findByText(/EmbeddingGemma 300M NPU runs on the NPU/)).toBeInTheDocument();
        expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('profile');
    });

    it('moves the embedder to the GPU when chosen', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('npu', false));
        mockedApi.setEmbedder.mockResolvedValue(view('gpu', true));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'gpu' } });
        await waitFor(() => expect(mockedApi.setEmbedder).toHaveBeenCalledExactlyOnceWith('gpu'));
        expect(await screen.findByRole('status')).toHaveTextContent(/Changed/);
        expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('gpu');
    });

    it('returns to the PC setup', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', true));
        mockedApi.setEmbedder.mockResolvedValue(view('npu', false));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'profile' } });
        await waitFor(() => expect(mockedApi.setEmbedder).toHaveBeenCalledExactlyOnceWith(null));
    });

    it('offers a device this PC lacks as unavailable', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', false, false));
        render(<EmbedderSetting />);
        const npu = await screen.findByRole('option', { name: /NPU \(not available on this PC\)/ });
        expect(npu).toBeDisabled();
    });

    it('shows why a change was refused', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('npu', false));
        mockedApi.setEmbedder.mockRejectedValue(new Error('A reply is running. Change the embedder once it finishes.'));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'gpu' } });
        expect(await screen.findByRole('alert')).toHaveTextContent('A reply is running');
    });
});
