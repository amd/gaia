// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelChip } from '../ModelChip';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { ActiveModel, ProviderInfo, ProviderModel } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const LOCAL_ACTIVE: ActiveModel = {
    provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
};
const CLOUD_ACTIVE: ActiveModel = {
    provider: 'fireworks', model: 'fireworks.accounts/fireworks/models/kimi-k2', label: 'Kimi', remote: true, is_default: false,
};

const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const FIREWORKS: ProviderInfo = {
    id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true,
    privacy_notice: 'Chat history is sent to Fireworks AI.',
};
const AMD_UNCONNECTED: ProviderInfo = {
    id: 'amd', name: 'AMD LLM Gateway', remote: true, models_discovered: false, privacy_notice: 'Sent to the gateway.',
};

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return { id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, ...over };
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null });
    useChatStore.setState({ settingsSection: null });
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, FIREWORKS, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async (id) => ({
        provider: id,
        models: id === 'local'
            ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', { downloaded: false })]
            : [model('fireworks.accounts/fireworks/models/kimi-k2', { rank: 1, note: 'best overall' })],
    }));
    mockedApi.selectModel.mockResolvedValue(CLOUD_ACTIVE);
});

async function openPicker() {
    fireEvent.click(screen.getByRole('button', { name: /Gemma|kimi|Model/ }));
    return screen.findByRole('dialog', { name: 'Choose a model' });
}

describe('ModelChip', () => {
    it('labels a local model with Local', () => {
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF · Local/ })).toBeInTheDocument();
    });

    it('labels a cloud model with its provider', () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /kimi-k2 · Fireworks AI/ })).toBeInTheDocument();
    });

    it('lists models from connected providers only', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        await within(dialog).findByText('Fireworks AI');

        expect(within(dialog).getByText('Local')).toBeInTheDocument();
        expect(within(dialog).queryByText('AMD LLM Gateway')).not.toBeInTheDocument();
        expect(mockedApi.listProviderModels).not.toHaveBeenCalledWith('amd');
        expect(within(dialog).getByRole('button', { name: /kimi-k2/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('button', { name: /Gemma-4-E4B-it-GGUF/ })).toHaveAttribute('aria-current', 'true');
        expect(within(dialog).getByRole('button', { name: /Qwen3-8B-GGUF/ })).toBeDisabled();
    });

    it('asks before moving a local chat to a cloud model, then switches', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('button', { name: /kimi-k2/ }));

        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Chat history is sent to Fireworks AI.');

        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.accounts/fireworks/models/kimi-k2'));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByRole('button', { name: /kimi-k2 · Fireworks AI/ })).toBeInTheDocument();
    });

    it('switches between local models without a notice', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id, models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF')] : [],
        }));
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('button', { name: /Qwen3-8B-GGUF/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('shows why a switch failed', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error('Model failed to load'));
        useModelStore.setState({ active: CLOUD_ACTIVE });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('button', { name: /Gemma-4-E4B-it-GGUF/ }));
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Model failed to load');
    });

    it('surfaces a model-server error instead of an empty list', async () => {
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL], active: LOCAL_ACTIVE, lemonade_error: 'Lemonade is not running' });
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Lemonade is not running');
    });

    it('opens model settings from the picker', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Providers and keys…' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });
});
