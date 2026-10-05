// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelChip } from '../ModelChip';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { ActiveModel, ProviderInfo, ProviderModel, Session } from '../../types';

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

// The backend says this chat is answered on this PC.
const SESSION: Session = {
    id: 's1', title: 'Chat', created_at: '', updated_at: '', model: 'Gemma-4-E4B-it-GGUF',
    system_prompt: null, message_count: 0, document_ids: [], inference_remote: false,
};

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return { id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, ...over };
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null });
    useChatStore.setState({ settingsSection: null, sessions: [SESSION], currentSessionId: SESSION.id });
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, FIREWORKS, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async (id) => ({
        provider: id,
        models: id === 'local'
            ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', { downloaded: false })]
            : [model('fireworks.accounts/fireworks/models/kimi-k2', { rank: 1, note: 'best overall' })],
    }));
    mockedApi.selectModel.mockResolvedValue(CLOUD_ACTIVE);
    mockedApi.listSessions.mockResolvedValue({ sessions: [SESSION], total: 1 });
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

    it('does not claim Local when the backend cannot tell where chat runs', () => {
        useChatStore.setState({ sessions: [{ ...SESSION, inference_remote: null }] });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF/ });
        expect(chip.textContent).not.toMatch(/local/i);
    });

    it('names the cloud provider the backend reports for a non-cloud pick', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, inference_remote: true, inference_provider_name: 'Fireworks AI', inference_description: 'Sent to Fireworks AI.' }],
        });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF · Fireworks AI/ });
        expect(chip.getAttribute('title')).toBe('Sent to Fireworks AI.');
    });

    it('labels a cloud model with its provider', () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /kimi-k2 · Fireworks AI/ })).toBeInTheDocument();
    });

    it("names the open chat's model, not the picked one", () => {
        useChatStore.setState({ sessions: [{ ...SESSION, effective_model: 'Qwen3-30B-A3B-Instruct-2507-GGUF' }] });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Qwen3-30B-A3B-Instruct-2507-GGUF · Local/ });
        expect(chip.textContent).not.toContain('Gemma');
    });

    it('names the model the backend resolves when it differs from the stored one', () => {
        useChatStore.setState({ sessions: [{ ...SESSION, model: 'qwen', effective_model: 'my-custom-model' }] });
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /my-custom-model · Local/ })).toBeInTheDocument();
    });

    it('names the Claude model a chat runs under the eval provider', () => {
        useChatStore.setState({
            sessions: [{
                ...SESSION, effective_model: 'claude-sonnet-4-5', inference_remote: true,
                inference_provider: 'claude', inference_provider_name: 'Anthropic',
            }],
        });
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /claude-sonnet-4-5 · Anthropic/ })).toBeInTheDocument();
    });

    it('names the picked model when no chat is open', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, effective_model: 'Qwen3-30B-A3B-Instruct-2507-GGUF' }],
            currentSessionId: null,
        });
        render(<ModelChip />);
        expect(screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF · Local/ })).toBeInTheDocument();
    });

    it("shows the chat's new model as soon as a switch lands", async () => {
        useChatStore.setState({ sessions: [{ ...SESSION, effective_model: 'Gemma-4-E4B-it-GGUF' }] });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF', is_default: false });
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id, models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF')] : [],
        }));
        mockedApi.listSessions.mockResolvedValue({
            sessions: [{ ...SESSION, effective_model: 'Qwen3-8B-GGUF' }], total: 1,
        });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('button', { name: /Qwen3-8B-GGUF/ }));

        expect(await screen.findByRole('button', { name: /Qwen3-8B-GGUF · Local/ })).toBeInTheDocument();
        expect(mockedApi.listSessions).toHaveBeenCalledTimes(1);
    });

    it('still switches when the chat list cannot be refreshed', async () => {
        mockedApi.listSessions.mockRejectedValue(new Error('backend restarting'));
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('button', { name: /kimi-k2/ }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));

        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
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
