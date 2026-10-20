// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The model picker during and after a turn (#5209), each local model's state,
 * device and fit (#5209, #5214), and friendly names.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelChip } from '../ModelChip';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { ActiveModel, ProviderInfo, ProviderModel, Session } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const FLM = 'gemma4-it-e2b-FLM';

const ACTIVE: ActiveModel = {
    provider: 'local', model: GEMMA, display_name: 'Gemma 4 E4B', label: 'Gemma', remote: false,
    is_default: true, downloaded: true,
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const FIREWORKS_NO_KEY: ProviderInfo = {
    id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: false, privacy_notice: 'Sent to Fireworks AI.',
};
const AMD_UNREGISTERED: ProviderInfo = {
    id: 'amd', name: 'AMD LLM Gateway', remote: true, registered: false, models_discovered: false, privacy_notice: 'x',
};
const SESSION: Session = {
    id: 's1', title: 'Chat', created_at: '', updated_at: '', model: GEMMA,
    system_prompt: null, message_count: 0, document_ids: [], inference_remote: false,
};

function local(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return {
        id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null,
        display_name: id, state: 'downloaded', available: true, unavailable_reason: null, fits: true,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'expected', warning: null },
        ...over,
    };
}

const MODELS: ProviderModel[] = [
    local(GEMMA, {
        display_name: 'Gemma 4 E4B', state: 'loaded', size_gb: 5.6, tokens_per_second: 41,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
    }),
    local(QWEN, { display_name: 'Qwen3.6 35B A3B', state: 'not_downloaded', downloaded: false, size_gb: 21.7 }),
    local('Qwen3-4B-GGUF', { display_name: 'Qwen3 4B' }),
    local(FLM, {
        display_name: 'Gemma 4 E2B', available: false, unavailable_reason: 'needs an AMD Ryzen AI NPU and its driver',
        placement: { device: 'npu', backend: 'flm', label: 'NPU · FLM', source: 'expected', warning: null },
    }),
];

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: ACTIVE, error: null, restoreNotice: null, queued: null, queueError: null, names: {} });
    useChatStore.setState({ settingsSection: null, sessions: [SESSION], currentSessionId: SESSION.id, isStreaming: false });
    mockedApi.listProviders.mockResolvedValue({
        providers: [LOCAL, FIREWORKS_NO_KEY, AMD_UNREGISTERED], active: ACTIVE, lemonade_error: null,
    });
    mockedApi.listProviderModels.mockResolvedValue({ provider: 'local', models: MODELS });
    mockedApi.selectModel.mockImplementation(async (model) => ({ ...ACTIVE, model, is_default: false }));
    mockedApi.listSessions.mockResolvedValue({ sessions: [SESSION], total: 1 });
    mockedApi.downloadModel.mockResolvedValue({ status: 'downloading', model: QWEN });
});

async function openPicker() {
    fireEvent.click(screen.getByRole('button', { name: /Gemma 4 E4B/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
    await within(dialog).findByText('Qwen3 4B');
    return dialog;
}

describe('ModelChip during a turn', () => {
    it('opens while a reply runs and says the pick is for the next message', async () => {
        useChatStore.setState({ isStreaming: true });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Gemma 4 E4B/ });
        expect(chip).toBeEnabled();
        const dialog = await openPicker();
        expect(within(dialog).getByRole('status')).toHaveTextContent('This reply keeps Gemma 4 E4B');
    });

    it('queues a pick, shows it on the chip, and applies it when the reply ends', async () => {
        useChatStore.setState({ isStreaming: true });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(within(dialog).getByRole('button', { name: /Qwen3 4B/ }));

        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(useModelStore.getState().queued).toBe('Qwen3-4B-GGUF');
        expect(screen.getByRole('button', { name: /Qwen3 4B next/ })).toBeInTheDocument();

        act(() => useChatStore.setState({ isStreaming: false }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-4B-GGUF'));
        await waitFor(() => expect(useModelStore.getState().queued).toBeNull());
    });

    it('says so when the queued model cannot be applied', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error('the model is not downloaded'));
        useModelStore.setState({ queued: 'Qwen3-4B-GGUF', names: { 'Qwen3-4B-GGUF': 'Qwen3 4B' } });
        useChatStore.setState({ isStreaming: true });
        act(() => useChatStore.setState({ isStreaming: false }));
        await waitFor(() => expect(useModelStore.getState().queueError).toMatch(/Couldn't switch to Qwen3 4B/));
    });

    it('switches at once when no reply is running', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(within(dialog).getByRole('button', { name: /Qwen3 4B/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-4B-GGUF'));
        expect(useModelStore.getState().queued).toBeNull();
    });
});

describe('ModelChip model states', () => {
    it('shows exactly one state per local model, each with a text label', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        const row = (name: RegExp) => within(dialog).getByRole('button', { name }).closest('.popover-row') as HTMLElement;
        expect(within(row(/^Gemma 4 E4B/)).getByRole('img', { name: 'Loaded' })).toBeInTheDocument();
        expect(within(row(/^Qwen3 4B/)).getByTitle('Downloaded, ready to load')).toHaveTextContent('Ready');
        expect(within(row(/^Qwen3.6 35B A3B/)).getByRole('button', { name: 'Download Qwen3.6 35B A3B' })).toBeInTheDocument();
    });

    it('downloads with progress, then shows the new state', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'local',
            models: MODELS.map((m) => (m.id === QWEN
                ? { ...m, download_progress: { state: 'downloading', model_name: QWEN, percent: 42, file: null, file_index: 0, total_files: 1, downloaded_bytes: 0, total_bytes: 0, message: null } }
                : m)),
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Download Qwen3.6 35B A3B' }));
        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith(QWEN));
        expect(await within(dialog).findByTitle('Downloading, 42%')).toBeInTheDocument();
    });

    it('shows device, size and measured speed', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(within(dialog).getByText('GPU · Vulkan · 5.6 GB · 41 tok/s on this PC')).toBeInTheDocument();
        expect(within(dialog).getByText(/GPU · Vulkan \(expected\) · 21.7 GB/)).toBeInTheDocument();
    });

    it('leaves out models this PC cannot run and says how many', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(within(dialog).queryByText('Gemma 4 E2B')).not.toBeInTheDocument();
        expect(within(dialog).getByText(/1 model isn’t available on this PC/)).toBeInTheDocument();
    });

    it('warns when a loaded model runs on another device than expected', async () => {
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'local',
            models: [local('Qwen3-4B-GGUF', {
                display_name: 'Qwen3 4B', state: 'loaded',
                placement: { device: 'cpu', backend: 'cpu', label: 'CPU', source: 'reported', warning: 'Running on the CPU instead of the GPU, so replies will be slow.' },
            })],
        });
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(within(dialog).getByText(/Running on the CPU instead of the GPU/)).toBeInTheDocument();
    });

    it('offers Fireworks with Add your key, and hides an unregistered gateway', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(within(dialog).getByText('Fireworks AI')).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole('button', { name: /Add your key/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(screen.queryByText('AMD LLM Gateway')).not.toBeInTheDocument();
        expect(mockedApi.listProviderModels).not.toHaveBeenCalledWith('fireworks');
    });

    it('flags a default that is not downloaded on the chip', () => {
        useModelStore.setState({ active: { ...ACTIVE, model: QWEN, display_name: 'Qwen3.6 35B A3B', downloaded: false } });
        useChatStore.setState({ sessions: [{ ...SESSION, effective_model: QWEN }] });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Qwen3.6 35B A3B/ });
        expect(chip).toHaveAttribute('title', expect.stringMatching(/isn't downloaded yet/));
    });
});
