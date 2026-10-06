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
    useChatStore.setState({ systemStatus: null, detectedDevices: ['gpu'] });
    withProviders(fireworks());
    mockedApi.listProviderModels.mockResolvedValue({ provider: 'local', models: [] });
    mockedApi.getActiveModel.mockResolvedValue(LOCAL_ACTIVE);
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

    it('connects with the typed key and clears the field', async () => {
        mockedApi.connectProvider.mockResolvedValue({ ...fireworks(), remembered: true, remember_error: null });
        const card = await openFireworks();
        const field = within(card).getByLabelText('Fireworks AI API key');
        fireEvent.change(field, { target: { value: 'fw-secret' } });
        fireEvent.click(within(card).getByRole('button', { name: 'Connect' }));

        await waitFor(() => expect(mockedApi.connectProvider).toHaveBeenCalledWith('fireworks', { api_key: 'fw-secret' }));
        expect(await within(card).findByRole('status')).toHaveTextContent('The key is kept for next time.');
        expect(field).toHaveValue('');
    });

    it('shows why the connection was refused', async () => {
        mockedApi.connectProvider.mockRejectedValue(new Error('Fireworks rejected the key (401)'));
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
