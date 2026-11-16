// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Hybrid in the model picker and in Settings > Model: its group, its place, and its tab. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelChip } from '../ModelChip';
import { ModelSettings } from '../settings/ModelSettings';
import { providerTabOf, useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { mockSetupApis } from '../../test/setupApiMocks';
import type { ActiveModel, HybridSettings, ProviderInfo, ProviderModel } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const LOCAL_ACTIVE: ActiveModel = {
    provider: 'local', model: GEMMA, display_name: 'Gemma 4 E4B', label: 'Gemma', remote: false, is_default: true, downloaded: true,
};
// The backend files the unprefixed `hybrid` id under local.
const HYBRID_ACTIVE: ActiveModel = {
    provider: 'local', model: 'hybrid', display_name: 'Hybrid', label: 'Hybrid · AMD LLM Gateway', remote: true,
    provider_name: 'AMD LLM Gateway', is_default: false,
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const AMD: ProviderInfo = {
    id: 'amd', name: 'AMD LLM Gateway', remote: true, models_discovered: true, registered: true, key_source: 'stored',
    env_var: 'LEMONADE_AMD_API_KEY', privacy_notice: 'Chat history is sent to your configured AMD gateway.',
};

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return {
        id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, available: true, ...over,
    };
}

const LOCAL_MODELS = [model(GEMMA, { display_name: 'Gemma 4 E4B', state: 'loaded' })];
const AMD_MODELS = [
    model('amd.DeepSeek-V4.1-Flash', { display_name: 'DeepSeek-V4.1-Flash' }),
    model('hybrid', { display_name: 'Hybrid (DeepSeek + Opus)' }),
];

function hybridSettings(over: Partial<HybridSettings> = {}): HybridSettings {
    const rule = { cheap_max_difficulty: 3, allow_high_stakes: false };
    const rules = {
        write_prose: rule, answer_question: rule, analyze_data: rule, operate_tools: rule,
        change_code: rule, find_defects: rule, plan_or_design: rule, other: rule,
    };
    return {
        cheap_model: 'amd.DeepSeek-V4.1-Flash', strong_model: 'amd.Claude-Opus-5-5', classifier_model: null,
        classifier_timeout: 15, policy: 'quality', rules, presets: { quality: rules, balanced: rules },
        kinds: Object.keys(rules) as HybridSettings['kinds'], models: [], is_active: false, ...over,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockSetupApis(mockedApi);
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null, queued: null, queueError: null, names: {} });
    useChatStore.setState({ settingsSection: null, settingsProvider: null, systemStatus: null, sessions: [], currentSessionId: null, isStreaming: false });
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, AMD], active: LOCAL_ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async (id) => (id === 'amd'
        ? { provider: 'amd', models: AMD_MODELS, blocked: false }
        : { provider: 'local', models: LOCAL_MODELS }));
    mockedApi.selectModel.mockImplementation(async (m) => (m === 'hybrid' ? HYBRID_ACTIVE : { ...LOCAL_ACTIVE, model: m }));
    mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mockedApi.getHybridSettings.mockResolvedValue(hybridSettings());
    mockedApi.getHybridStats.mockResolvedValue({
        since: null, turns: 0, cheap_turns: 0, strong_turns: 0, failed_closed: 0, cost: 0, cost_if_all_strong: 0, saving_pct: null,
    });
    mockedApi.getSpending.mockResolvedValue({
        month: '2026-11', total_usd: 0, turns: 0, unpriced_turns: 0, unreported_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price',
    });
});

async function openPicker() {
    render(<ModelChip />);
    fireEvent.click(screen.getByRole('button', { name: /Gemma 4 E4B|Hybrid/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
    const gateway = await within(dialog).findByRole('group', { name: 'AMD LLM Gateway' });
    return { dialog, gateway };
}

describe('ModelChip: hybrid', () => {
    it('offers Hybrid under the AMD LLM Gateway, labelled cloud', async () => {
        const { gateway } = await openPicker();
        const row = await within(gateway).findByRole('option', { name: /Hybrid \(DeepSeek \+ Opus\)/ });
        await waitFor(() => expect(row).toHaveTextContent('Cloud'));
        expect(within(screen.getByRole('group', { name: 'Local' })).queryByText(/Hybrid/)).not.toBeInTheDocument();
    });

    it('asks the gateway’s consent like other gateway models, then makes it the model', async () => {
        const { gateway } = await openPicker();
        fireEvent.click(await within(gateway).findByRole('option', { name: /Hybrid/ }));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        fireEvent.click(await within(gateway).findByRole('button', { name: /^Use / }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('hybrid'));
        await waitFor(() => expect(useModelStore.getState().active?.model).toBe('hybrid'));
    });

    it('is labelled local, with no cloud consent, when every model it routes to runs on this PC', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({ cheap_model: 'Qwen3-4B-GGUF', strong_model: GEMMA }));
        const { gateway } = await openPicker();
        const row = await within(gateway).findByRole('option', { name: /Hybrid/ });
        await waitFor(() => expect(row).toHaveTextContent('Local'));
        fireEvent.click(row);
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('hybrid'));
    });

    it('counts a cloud classifier as cloud even when both routed models are local', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({
            cheap_model: 'Qwen3-4B-GGUF', strong_model: GEMMA, classifier_model: 'amd.DeepSeek-V4.1-Flash',
        }));
        const { gateway } = await openPicker();
        const row = await within(gateway).findByRole('option', { name: /Hybrid/ });
        await waitFor(() => expect(row).toHaveTextContent('Cloud'));
    });

    it('makes no place claim when the hybrid settings cannot be read', async () => {
        mockedApi.getHybridSettings.mockRejectedValue(new Error('hybrid_rules is required when hybrid_policy is custom.'));
        const { gateway } = await openPicker();
        const row = await within(gateway).findByRole('option', { name: /Hybrid/ });
        await waitFor(() => expect(mockedApi.getHybridSettings).toHaveBeenCalled());
        expect(row).not.toHaveTextContent(/Cloud|Local/);
    });

    it('lists the gateway first while a hybrid chat is in use', async () => {
        useModelStore.setState({ active: HYBRID_ACTIVE });
        const { dialog } = await openPicker();
        const headings = within(dialog).getAllByRole('group').map((g) => g.getAttribute('aria-labelledby'));
        expect(document.getElementById(headings[0]!)).toHaveTextContent('AMD LLM Gateway');
    });
});

describe('ModelSettings: hybrid', () => {
    it('opens on the AMD LLM Gateway tab, not Local, while hybrid is in use', async () => {
        useModelStore.setState({ active: HYBRID_ACTIVE });
        render(<ModelSettings />);
        expect(await screen.findByRole('radio', { name: /AMD LLM Gateway/ })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByRole('radio', { name: /Local/ })).toHaveAttribute('aria-checked', 'false');
        // The Hybrid panel sits under the gateway once it offers hybrid.
        expect(await screen.findByText('Each request runs on a cheap model or a strong one')).toBeInTheDocument();
    });

    it('maps the in-use model to its tab', () => {
        expect(providerTabOf(HYBRID_ACTIVE)).toBe('amd');
        expect(providerTabOf({ ...HYBRID_ACTIVE, model: ' Hybrid ' })).toBe('amd');
        expect(providerTabOf(LOCAL_ACTIVE)).toBe('local');
        expect(providerTabOf(null)).toBeUndefined();
    });
});
