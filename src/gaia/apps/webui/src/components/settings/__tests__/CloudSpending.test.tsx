// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Cloud prices before a model is picked, and the monthly spending limit (#5258). */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CloudPrices } from '../CloudPrices';
import { SpendingLimit } from '../SpendingLimit';
import { ModelSettings } from '../ModelSettings';
import { formatPrice } from '../cloudPrice';
import { ModelChip } from '../../ModelChip';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { ActiveModel, ModelPrice, ProviderInfo, SpendingReport } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const FLASH: ModelPrice = {
    input_per_million: 0.15, cached_input_per_million: 0.03, output_per_million: 0.5, source: 'Fireworks AI list price',
};
const REPORT: SpendingReport = {
    month: '2026-10', total_usd: 1.25, turns: 12, unpriced_turns: 0, limit_usd: null, state: 'no_limit',
    by_model: [], price_source: 'Fireworks AI list price',
};
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

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null });
    useChatStore.setState({ systemStatus: null, settingsSection: null, sessions: [], currentSessionId: null });
    mockedApi.getCloudPrices.mockImplementation(async (provider) => (provider === 'fireworks'
        ? {
            provider, source: 'Fireworks AI list price, checked 2026-09-19', note: null,
            models: [
                { model: 'fireworks.deepseek-v4p1-flash', name: 'deepseek-v4p1-flash', rank: 1, note: 'best overall, fastest', price: { ...FLASH, input_per_million: 0.3, output_per_million: 1.2 } },
                { model: 'fireworks.glm-5p3-flash', name: 'glm-5p3-flash', rank: 2, note: 'cheapest', price: FLASH },
            ],
        }
        : { provider, models: [], source: null, note: 'Prices are set by the organization that runs the gateway; GAIA doesn’t know them.' }));
    mockedApi.getSpending.mockResolvedValue(REPORT);
    mockedApi.setSpendingLimit.mockImplementation(async (limit) => ({ ...REPORT, limit_usd: limit, state: limit ? 'ok' : 'no_limit' }));
    mockedApi.allowSpendingThisMonth.mockResolvedValue({ ...REPORT, limit_usd: 1, state: 'allowed_over' });
    mockedApi.getContextChoice.mockResolvedValue({
        model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', chosen: null, automatic: 65536,
        min: 65536, max_fit: 65536, options: [65536], loaded_ctx: null, memory_source: 'AMD iGPU', note: null,
    });
});

describe('formatPrice', () => {
    it('says price unknown rather than $0', () => {
        expect(formatPrice(null)).toBe('price unknown');
        expect(formatPrice(undefined)).toBe('price unknown');
        expect(formatPrice(FLASH)).toBe('$0.15 in · $0.50 out per 1M tokens');
    });
});

describe('CloudPrices', () => {
    it('lists prices before connecting, recommended first', async () => {
        render(<CloudPrices provider="fireworks" />);
        expect(await screen.findByText('$0.30 in · $1.20 out per 1M tokens')).toBeInTheDocument();
        const items = screen.getAllByRole('listitem');
        expect(items[0]).toHaveTextContent('deepseek-v4p1-flash · best overall, fastest');
        expect(screen.getByText(/list price, checked 2026-09-19/)).toBeInTheDocument();
    });

    it('says the gateway price is unknown and why', async () => {
        render(<CloudPrices provider="amd" />);
        expect(await screen.findByText(/Price unknown\. Prices are set by the organization/)).toBeInTheDocument();
        expect(screen.queryByText(/\$0/)).not.toBeInTheDocument();
    });
});

describe('SpendingLimit', () => {
    it('shows the running total for the month', async () => {
        render(<SpendingLimit />);
        expect(await screen.findByText('$1.25')).toBeInTheDocument();
        expect(screen.getByText(/12 cloud chat turns, at list price/)).toBeInTheDocument();
    });

    it('counts turns of unknown price apart, never as $0', async () => {
        mockedApi.getSpending.mockResolvedValue({ ...REPORT, unpriced_turns: 3 });
        render(<SpendingLimit />);
        expect(await screen.findByText(/Plus 3 turns on models with an unknown price/)).toBeInTheDocument();
    });

    it('sets and removes a monthly limit', async () => {
        render(<SpendingLimit />);
        fireEvent.change(await screen.findByLabelText('Monthly limit ($)'), { target: { value: '10' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.setSpendingLimit).toHaveBeenCalledWith(10));
        expect(await screen.findByText(/of your \$10\.00 limit/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Remove limit' }));
        await waitFor(() => expect(mockedApi.setSpendingLimit).toHaveBeenLastCalledWith(null));
    });

    it('refuses a limit of $0', async () => {
        render(<SpendingLimit />);
        fireEvent.change(await screen.findByLabelText('Monthly limit ($)'), { target: { value: '0' } });
        expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    });

    it('warns near the limit', async () => {
        mockedApi.getSpending.mockResolvedValue({ ...REPORT, limit_usd: 1.5, state: 'near' });
        render(<SpendingLimit />);
        expect(await screen.findByRole('status')).toHaveTextContent('Close to your limit');
    });

    it('says cloud chats stopped at the limit, and offers to allow more this month', async () => {
        mockedApi.getSpending.mockResolvedValue({ ...REPORT, limit_usd: 1, state: 'reached' });
        render(<SpendingLimit />);
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('cloud chats are stopped until next month');
        fireEvent.click(within(alert).getByRole('button', { name: 'Allow more this month' }));
        await waitFor(() => expect(mockedApi.allowSpendingThisMonth).toHaveBeenCalled());
        expect(await screen.findByText(/you allowed more for this month/)).toBeInTheDocument();
    });
});

describe('Settings › Model', () => {
    it('shows Fireworks prices before it is connected', async () => {
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks()], active: LOCAL_ACTIVE, lemonade_error: null });
        mockedApi.listProviderModels.mockResolvedValue({ provider: 'local', models: [] });
        render(<ModelSettings />);
        fireEvent.click(await screen.findByRole('radio', { name: /Fireworks AI/ }));
        const card = await screen.findByRole('region', { name: 'Fireworks AI' });
        expect(await within(card).findByText('$0.15 in · $0.50 out per 1M tokens')).toBeInTheDocument();
        expect(await within(card).findByText('Cloud spending this month')).toBeInTheDocument();
    });

    it('shows each connected model’s price, or price unknown', async () => {
        mockedApi.listProviders.mockResolvedValue({
            providers: [LOCAL, fireworks({ models_discovered: true, key_source: 'stored' })], active: LOCAL_ACTIVE, lemonade_error: null,
        });
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'fireworks',
            models: [
                { id: 'fireworks.glm-5p3-flash', context_length: null, downloaded: true, labels: [], rank: 2, note: 'cheapest', evidence: null, price: FLASH },
                { id: 'fireworks.new-model', context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, price: null },
            ],
        });
        render(<ModelSettings />);
        fireEvent.click(await screen.findByRole('radio', { name: /Fireworks AI/ }));
        const card = await screen.findByRole('region', { name: 'Fireworks AI' });
        expect(await within(card).findByText('$0.15 in · $0.50 out per 1M tokens')).toBeInTheDocument();
        expect(within(card).getByText('price unknown')).toBeInTheDocument();
    });
});

describe('ModelChip', () => {
    it('shows a cloud model’s price in the picker', async () => {
        mockedApi.listProviders.mockResolvedValue({
            providers: [LOCAL, fireworks({ models_discovered: true })], active: LOCAL_ACTIVE, lemonade_error: null,
        });
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local' ? [] : [
                { id: 'fireworks.glm-5p3-flash', context_length: null, downloaded: true, labels: [], rank: 2, note: 'cheapest', evidence: null, price: FLASH },
            ],
        }));
        render(<ModelChip />);
        fireEvent.click(screen.getByRole('button', { name: /Gemma|Model/ }));
        const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
        fireEvent.mouseEnter(await within(dialog).findByRole('button', { name: /^About glm-5p3-flash/i }));
        const bubble = await screen.findByRole('tooltip');
        expect(bubble).toHaveTextContent('#2 cheapest');
        expect(bubble).toHaveTextContent('$0.15 in · $0.50 out per 1M tokens');
    });
});
