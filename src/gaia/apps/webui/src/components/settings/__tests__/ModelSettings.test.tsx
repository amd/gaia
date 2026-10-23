// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelSettings } from '../ModelSettings';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { ActiveModel, ProviderInfo } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const LOCAL_ACTIVE: ActiveModel = {
    provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };

function fireworks(over: Partial<ProviderInfo> = {}): ProviderInfo {
    return {
        id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: false, key_source: null,
        env_var: 'FIREWORKS_API_KEY', privacy_notice: 'Chat history is sent to Fireworks AI.', ...over,
    };
}

function withProviders(fw: ProviderInfo) {
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fw], active: LOCAL_ACTIVE, lemonade_error: null });
}

async function openFireworks() {
    render(<ModelSettings />);
    fireEvent.click(await screen.findByRole('radio', { name: /Fireworks AI/ }));
    return screen.findByRole('region', { name: 'Fireworks AI' });
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null });
    mockedApi.getCloudPrices.mockResolvedValue({ provider: 'fireworks', models: [], source: null, note: 'Prices are set by the organization that runs the gateway.' });
    mockedApi.getSpending.mockResolvedValue({
        month: '2026-10', total_usd: 0, turns: 0, unpriced_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price',
    });
    useChatStore.setState({ systemStatus: null });
    withProviders(fireworks());
    mockedApi.listProviderModels.mockResolvedValue({ provider: 'local', models: [] });
    mockedApi.getActiveModel.mockResolvedValue(LOCAL_ACTIVE);
    mockedApi.getContextChoice.mockResolvedValue({
        model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', chosen: null, automatic: 65536,
        min: 65536, max_fit: 65536, options: [65536], loaded_ctx: null, memory_source: 'AMD iGPU', note: null,
    });
});

describe('ModelSettings provider keys', () => {
    it('takes the key in a password field', async () => {
        const card = await openFireworks();
        expect(within(card).getByLabelText('Fireworks AI API key')).toHaveAttribute('type', 'password');
        expect(within(card).getByText('No key yet.')).toBeInTheDocument();
        expect(within(card).getByRole('button', { name: 'Connect' })).toBeDisabled();
        expect(within(card).queryByRole('button', { name: 'Forget key' })).not.toBeInTheDocument();
    });

    it('says when the model server already holds a key, and offers to forget it', async () => {
        withProviders(fireworks({ key_source: 'lemonade' }));
        const card = await openFireworks();
        expect(within(card).getByText(/A key is already configured/)).toBeInTheDocument();
        expect(within(card).getByRole('button', { name: 'Connect' })).toBeEnabled();
        expect(within(card).getByRole('button', { name: 'Forget key' })).toBeInTheDocument();
    });

    it('says the OS is asking for keychain access instead of "No key yet", then updates', async () => {
        mockedApi.listProviders
            .mockResolvedValueOnce({
                providers: [LOCAL, fireworks({
                    key_state: 'waiting_for_os_permission',
                    key_message: 'macOS is asking GAIA for keychain access to read your saved Fireworks AI key.',
                })],
                active: LOCAL_ACTIVE,
                lemonade_error: null,
            })
            .mockResolvedValue({ providers: [LOCAL, fireworks({ key_source: 'stored' })], active: LOCAL_ACTIVE, lemonade_error: null });
        const card = await openFireworks();
        expect(within(card).getByText(/asking GAIA for keychain access/)).toBeInTheDocument();
        expect(within(card).queryByText('No key yet.')).not.toBeInTheDocument();
        expect(await within(card).findByText('A saved key will be used.', {}, { timeout: 4000 })).toBeInTheDocument();
    });

    it('connects with the typed key and clears the field', async () => {
        mockedApi.connectProviderWaitingForOs.mockResolvedValue({ ...fireworks(), remembered: true, remember_error: null });
        const card = await openFireworks();
        const field = within(card).getByLabelText('Fireworks AI API key');
        fireEvent.change(field, { target: { value: 'fw-secret' } });
        fireEvent.click(within(card).getByRole('button', { name: 'Connect' }));

        await waitFor(() => expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('fireworks', { api_key: 'fw-secret' }, expect.any(Function)));
        expect(await within(card).findByRole('status')).toHaveTextContent('The key is kept for next time.');
        expect(field).toHaveValue('');
    });

    it('shows why the connection was refused', async () => {
        mockedApi.connectProviderWaitingForOs.mockRejectedValue(new Error('Fireworks rejected the key (401)'));
        const card = await openFireworks();
        fireEvent.change(within(card).getByLabelText('Fireworks AI API key'), { target: { value: 'bad' } });
        fireEvent.click(within(card).getByRole('button', { name: 'Connect' }));
        expect(await within(card).findByRole('alert')).toHaveTextContent('Fireworks rejected the key (401)');
    });

    it('forgets a stored key', async () => {
        withProviders(fireworks({ key_source: 'stored' }));
        mockedApi.forgetProviderKey.mockResolvedValue({ removed: true });
        const card = await openFireworks();
        fireEvent.click(within(card).getByRole('button', { name: 'Forget key' }));
        await waitFor(() => expect(mockedApi.forgetProviderKey).toHaveBeenCalledWith('fireworks'));
        expect(await within(card).findByRole('status')).toHaveTextContent('Key cleared');
    });
});

describe('ModelSettings: an unreachable AMD gateway (#5604)', () => {
    const NO_NET = "No network available — can't reach llm-api.amd.com (needs AMD's network or VPN)";
    function amd(over: Partial<ProviderInfo> = {}): ProviderInfo {
        return {
            id: 'amd', name: 'AMD LLM Gateway', remote: true, models_discovered: false, key_source: null,
            env_var: 'LEMONADE_AMD_API_KEY', privacy_notice: 'Chat history is sent to your configured AMD gateway.',
            network: { reachable: false, detail: NO_NET }, ...over,
        };
    }

    it('greys out the gateway tab and says there is no network', async () => {
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks(), amd()], active: LOCAL_ACTIVE, lemonade_error: null });
        render(<ModelSettings />);
        const tab = await screen.findByRole('radio', { name: /AMD LLM Gateway/ });
        expect(tab).toBeDisabled();
        expect(tab).toHaveAttribute('title', NO_NET);
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toBeEnabled();
    });

    it('on the gateway, shows no key form until the network is back, and checks again', async () => {
        const amdActive: ActiveModel = { provider: 'amd', model: 'amd.DeepSeek-V4.1-Flash', label: 'DeepSeek', remote: true, is_default: false };
        useModelStore.setState({ active: amdActive, error: null, restoreNotice: null });
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks(), amd()], active: amdActive, lemonade_error: null });
        render(<ModelSettings />);
        const card = await screen.findByRole('region', { name: 'AMD LLM Gateway' });
        expect(await within(card).findByRole('alert')).toHaveTextContent(NO_NET);
        expect(within(card).queryByLabelText('AMD LLM Gateway API key')).not.toBeInTheDocument();

        mockedApi.listProviders.mockResolvedValue({
            providers: [LOCAL, fireworks(), amd({ network: { reachable: true, detail: null } })], active: amdActive, lemonade_error: null,
        });
        fireEvent.click(within(card).getByRole('button', { name: 'Check again' }));
        expect(await within(card).findByLabelText('AMD LLM Gateway API key')).toBeInTheDocument();
    });
});
