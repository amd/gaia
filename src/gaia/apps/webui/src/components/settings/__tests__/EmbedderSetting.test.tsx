// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EmbedderSetting } from '../EmbedderSetting';
import * as memoryApi from '../../../services/memoryApi';

vi.mock('../../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

function view(
    device: memoryApi.EmbedderDevice,
    chosen: boolean,
    { gpuAvailable = true, changed }: { gpuAvailable?: boolean; changed?: boolean } = {},
): memoryApi.EmbedderView {
    return {
        device,
        chosen,
        default_device: 'gpu',
        changed,
        options: [
            { device: 'gpu', model: 'user.embeddinggemma-300m-GGUF', display_name: 'EmbeddingGemma 300M', proven: true, present: gpuAvailable, available: gpuAvailable, reason: gpuAvailable ? null : 'no Vulkan', downloaded: true },
            { device: 'npu', model: 'embed-gemma-300m-FLM', display_name: 'EmbeddingGemma 300M NPU', proven: false, present: true, available: false, reason: 'not reliable yet', downloaded: true },
        ],
    };
}

beforeEach(() => {
    vi.clearAllMocks();
});

describe('EmbedderSetting', () => {
    it('says which embedder runs and on which device', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', false));
        render(<EmbedderSetting />);
        // The model name is bidi-isolated, so it reads whole in a right-to-left line.
        expect(await screen.findByText(/EmbeddingGemma 300M⁩? runs on the GPU/)).toBeInTheDocument();
        expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('profile');
        expect(screen.getByRole('option', { name: 'Default (GPU)' })).toBeInTheDocument();
    });

    it('shows the NPU but does not offer it, and says why', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', false));
        render(<EmbedderSetting />);
        expect(await screen.findByRole('option', { name: 'NPU (not supported yet)' })).toBeDisabled();
        expect(screen.getByText(/Search with the NPU embedder isn't reliable yet/)).toBeInTheDocument();
    });

    it('moves a saved NPU choice back to the default', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('npu', true));
        mockedApi.setEmbedder.mockResolvedValue(view('gpu', false, { changed: true }));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'profile' } });
        await waitFor(() => expect(mockedApi.setEmbedder).toHaveBeenCalledExactlyOnceWith(null));
        expect(await screen.findByRole('status')).toHaveTextContent(/Changed/);
        expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('profile');
    });

    it('says nothing changed when the embedder stays the same', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', false));
        mockedApi.setEmbedder.mockResolvedValue(view('gpu', true, { changed: false }));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'gpu' } });
        await waitFor(() => expect(mockedApi.setEmbedder).toHaveBeenCalledExactlyOnceWith('gpu'));
        await waitFor(() => expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('gpu'));
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });

    it('on a PC without an NPU, says it is not available and skips the reason', async () => {
        const noNpu = view('gpu', false);
        noNpu.options[1] = { ...noNpu.options[1], present: false, reason: 'no NPU' };
        mockedApi.getEmbedder.mockResolvedValue(noNpu);
        render(<EmbedderSetting />);
        expect(await screen.findByRole('option', { name: 'NPU (not available on this PC)' })).toBeDisabled();
        expect(screen.queryByText(/Search with the NPU embedder isn't reliable yet/)).not.toBeInTheDocument();
    });

    it('offers a device this PC lacks as unavailable', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('gpu', false, { gpuAvailable: false }));
        render(<EmbedderSetting />);
        expect(await screen.findByRole('option', { name: /GPU \(not available on this PC\)/ })).toBeDisabled();
    });

    it('shows why a change was refused, and what is saved now', async () => {
        mockedApi.getEmbedder.mockResolvedValue(view('npu', true));
        mockedApi.setEmbedder.mockRejectedValue(new Error('A reply is running. Change the embedder once it finishes.'));
        render(<EmbedderSetting />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Embedding model' }), { target: { value: 'profile' } });
        expect(await screen.findByRole('alert')).toHaveTextContent('A reply is running');
        await waitFor(() => expect(mockedApi.getEmbedder).toHaveBeenCalledTimes(2));
        expect(screen.getByRole('combobox', { name: 'Embedding model' })).toHaveValue('npu');
    });
});
