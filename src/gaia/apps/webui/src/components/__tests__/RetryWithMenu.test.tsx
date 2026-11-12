// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RetryWithMenu } from '../RetryWithMenu';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { ProviderInfo, ProviderModel } from '../../types';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const FIREWORKS: ProviderInfo = { id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true, privacy_notice: 'x' };

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return { id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, ...over };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, FIREWORKS], active: null as never, lemonade_error: null });
    mockedApi.listProviderModels.mockResolvedValue({
        provider: 'local',
        models: [
            model('Gemma-4-E4B-it-GGUF', { display_name: 'Gemma 4 E4B', size_gb: 5.6 }),
            model('Qwen3-8B-GGUF', { display_name: 'Qwen3 8B', downloaded: false, size_gb: 5.2 }),
        ],
    });
});

async function openMenu() {
    fireEvent.click(screen.getByRole('button', { name: 'Retry with another model' }));
    return screen.findByRole('dialog');
}

describe('RetryWithMenu', () => {
    it('lists downloaded models on this PC with their size', async () => {
        useModelStore.setState({ active: { provider: 'local', model: 'x', label: null, remote: false, is_default: true } });
        render(<RetryWithMenu answeredBy="x" onPick={vi.fn()} />);
        const menu = await openMenu();
        const item = await within(menu).findByRole('button', { name: /Gemma 4 E4B/ });
        expect(item).toHaveTextContent('5.6 GB');
        expect(within(menu).queryByText(/Qwen3 8B/)).not.toBeInTheDocument();
        expect(within(menu).queryByText(/not on/)).not.toBeInTheDocument();
    });

    it('tells a cloud chat that the retry runs on this PC', async () => {
        useModelStore.setState({
            active: { provider: 'fireworks', model: 'fireworks.glm', label: null, remote: true, is_default: false, provider_name: 'Fireworks AI' },
        });
        render(<RetryWithMenu answeredBy="fireworks.glm" onPick={vi.fn()} />);
        const menu = await openMenu();
        await within(menu).findByRole('button', { name: /Gemma 4 E4B/ });
        expect(within(menu).getByText(/These run on this PC, not on Fireworks AI/)).toBeInTheDocument();
    });
});
