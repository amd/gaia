// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings › Model for local models: no device switch (#5214), each model's
 * device, fit, size and speed, the models this PC cannot run (#5209), and the
 * context size (#5213).
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelSettings } from '../ModelSettings';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { ActiveModel, ContextChoice, ProviderInfo, ProviderModel } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const ACTIVE: ActiveModel = {
    provider: 'local', model: QWEN, display_name: 'Qwen3.6 35B A3B', label: 'Qwen', remote: false,
    is_default: true, downloaded: true,
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };

function local(id: string, over: Partial<ProviderModel>): ProviderModel {
    return {
        id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null,
        state: 'downloaded', available: true, unavailable_reason: null, fits: true, ...over,
    };
}

const MODELS = [
    local(QWEN, {
        display_name: 'Qwen3.6 35B A3B', state: 'loaded', size_gb: 21.7, tokens_per_second: 52,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
    }),
    local('Gemma-4-E4B-it-GGUF', {
        display_name: 'Gemma 4 E4B', size_gb: 5.6,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'expected', warning: null },
    }),
    local('gemma4-it-e2b-FLM', {
        display_name: 'Gemma 4 E2B', available: false, fits: true,
        unavailable_reason: 'needs an AMD Ryzen AI NPU and its driver (No NPU)',
        placement: { device: 'npu', backend: 'flm', label: 'NPU · FLM', source: 'expected', warning: null },
    }),
];

const CTX: ContextChoice = {
    model: QWEN, display_name: 'Qwen3.6 35B A3B', chosen: null, automatic: 262144, min: 65536,
    max_fit: 262144, options: [65536, 131072, 262144], loaded_ctx: 262144, memory_source: 'AMD iGPU', note: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: ACTIVE, error: null, restoreNotice: null, names: {} });
    useChatStore.setState({ systemStatus: null });
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL], active: ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockResolvedValue({
        provider: 'local', models: MODELS, memory_gb: 112, memory_source: 'AMD iGPU', capacity_error: null,
    });
    mockedApi.getContextChoice.mockResolvedValue(CTX);
    mockedApi.setContextChoice.mockImplementation(async (_m, ctx) => ({
        ...CTX, chosen: ctx, reload: { started: true, reason: null },
    }));
});

async function renderLocal() {
    render(<ModelSettings />);
    return screen.findByText('Gemma 4 E4B');
}

describe('ModelSettings local models', () => {
    it('has no CPU/GPU/NPU switch', async () => {
        await renderLocal();
        expect(screen.queryByText('Run local models on')).not.toBeInTheDocument();
        expect(screen.queryByRole('radiogroup', { name: 'Device' })).not.toBeInTheDocument();
    });

    it('shows each model\'s device, size, speed and fit', async () => {
        await renderLocal();
        const qwen = screen.getByText('Qwen3.6 35B A3B', { selector: '.model-row-name' }).closest('li') as HTMLElement;
        expect(within(qwen).getByText('GPU · Vulkan · 21.7 GB download · 52 tok/s measured here')).toBeInTheDocument();
        expect(within(qwen).getByText('Fits this PC')).toBeInTheDocument();
        expect(within(qwen).getByText('Loaded')).toBeInTheDocument();
        const gemma = screen.getByText('Gemma 4 E4B').closest('li') as HTMLElement;
        expect(within(gemma).getByText(/GPU · Vulkan \(expected\)/)).toBeInTheDocument();
    });

    it('lists models this PC cannot run separately, with why', async () => {
        await renderLocal();
        const section = screen.getByText(/Not available on this PC \(1\)/).closest('details') as HTMLElement;
        expect(within(section).getByText('Gemma 4 E2B')).toBeInTheDocument();
        expect(within(section).getByText(/needs an AMD Ryzen AI NPU/)).toBeInTheDocument();
        expect(within(section).queryByRole('button', { name: 'Use' })).not.toBeInTheDocument();
    });

    it('offers context sizes up to what fits, with Automatic first', async () => {
        await renderLocal();
        const select = await screen.findByLabelText('Context size');
        const options = within(select).getAllByRole('option').map((o) => o.textContent);
        expect(options).toEqual(['Automatic (256K)', '64K tokens', '128K tokens', '256K tokens']);
        expect(screen.getByText(/Up to 256K, the most Qwen3.6 35B A3B can hold on this PC/)).toBeInTheDocument();
    });

    it('saves a size; the backend reloads the model through its checked path', async () => {
        await renderLocal();
        fireEvent.change(await screen.findByLabelText('Context size'), { target: { value: '131072' } });
        await waitFor(() => expect(mockedApi.setContextChoice).toHaveBeenCalledWith(QWEN, 131072));
        expect(await screen.findByText(/Reloading Qwen3.6 35B A3B at 128K/)).toBeInTheDocument();
        expect(mockedApi.loadModel).not.toHaveBeenCalled();
    });

    it('waits for a running reply instead of reloading under it', async () => {
        mockedApi.setContextChoice.mockResolvedValue({ ...CTX, chosen: 131072, reload: { started: false, reason: 'busy' } });
        await renderLocal();
        fireEvent.change(await screen.findByLabelText('Context size'), { target: { value: '131072' } });
        expect(await screen.findByText(/GAIA is answering right now/)).toBeInTheDocument();
    });

    it('Automatic clears the saved size', async () => {
        mockedApi.getContextChoice.mockResolvedValue({ ...CTX, chosen: 131072 });
        await renderLocal();
        fireEvent.change(await screen.findByLabelText('Context size'), { target: { value: 'auto' } });
        await waitFor(() => expect(mockedApi.setContextChoice).toHaveBeenCalledWith(QWEN, null));
        expect(await screen.findByText(/Reloading Qwen3.6 35B A3B at the automatic size/)).toBeInTheDocument();
    });

    it('shows a refusal from the server', async () => {
        mockedApi.setContextChoice.mockRejectedValue(new Error('256K is not offered for this model on this PC.'));
        await renderLocal();
        fireEvent.change(await screen.findByLabelText('Context size'), { target: { value: '262144' } });
        expect(await screen.findByRole('alert')).toHaveTextContent('not offered');
    });
});
