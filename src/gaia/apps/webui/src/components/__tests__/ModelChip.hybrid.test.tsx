// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Hybrid in the model picker and in Settings > Model: its own entry, its name and place, and its tab. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelChip } from '../ModelChip';
import { ModelSettings } from '../settings/ModelSettings';
import { providerTabOf, useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { mockSetupApis } from '../../test/setupApiMocks';
import type { ActiveModel, HybridSettings, HybridTemplate, ProviderInfo, ProviderModel } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const LOCAL_ACTIVE: ActiveModel = {
    provider: 'local', model: GEMMA, display_name: 'Gemma 4 E4B', label: 'Gemma', remote: false, is_default: true, downloaded: true,
};
// The backend files the unprefixed `hybrid` id under local.
const HYBRID_ACTIVE: ActiveModel = {
    provider: 'local', model: 'hybrid', display_name: 'Hybrid', label: 'Hybrid', remote: true,
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
// An older backend still lists hybrid under the gateway; the picker shows it once, on its own.
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
        kinds: Object.keys(rules) as HybridSettings['kinds'],
        models: [
            { id: 'amd.DeepSeek-V4.1-Flash', display_name: 'DeepSeek', provider: 'amd', available: true },
            { id: 'amd.Claude-Opus-5-5', display_name: 'Opus', provider: 'amd', available: true },
            { id: 'fireworks.deepseek-v4p1-flash', display_name: 'DeepSeek Fireworks', provider: 'fireworks', available: true },
            { id: GEMMA, display_name: 'Gemma 4 E4B', provider: 'local', available: true },
            { id: QWEN, display_name: 'Qwen3.6 35B', provider: 'local', available: true },
        ],
        is_active: false,
        ...over,
    };
}

const TEMPLATE: HybridTemplate = {
    id: 'gateway-deepseek-opus', name: 'Gateway', description: 'x', cheap_model: 'amd.DeepSeek-V4.1-Flash',
    strong_model: 'amd.Claude-Opus-5-5', classifier_model: null, policy: 'quality', providers_needed: ['amd'], available: true, measured: true,
};

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
    mockedApi.getHybridTemplates.mockResolvedValue([TEMPLATE]);
    mockedApi.getHybridStats.mockResolvedValue({
        since: null, turns: 0, cheap_turns: 0, strong_turns: 0, failed_closed: 0, cost: 0, cost_if_all_strong: 0, saving_pct: null,
    });
    mockedApi.getContextChoice.mockResolvedValue({
        model: GEMMA, display_name: 'Gemma 4 E4B', chosen: null, automatic: 65536,
        min: 65536, max_fit: 65536, options: [65536], loaded_ctx: null, memory_source: 'AMD iGPU', note: null,
    });
    mockedApi.getSpending.mockResolvedValue({
        month: '2026-11', total_usd: 0, turns: 0, unpriced_turns: 0, unreported_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price',
    });
});

async function openPicker() {
    render(<ModelChip />);
    fireEvent.click(screen.getByRole('button', { name: /Gemma 4 E4B|Hybrid/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
    await within(dialog).findByRole('group', { name: 'AMD LLM Gateway' });
    return dialog;
}

describe('ModelChip: hybrid', () => {
    it('is its own entry, named from the configured pair, not a row of the gateway', async () => {
        const dialog = await openPicker();
        const group = await within(dialog).findByRole('group', { name: 'Hybrid' });
        const row = within(group).getByRole('option', { name: /Hybrid \(DeepSeek \+ Opus\)/ });
        expect(row).toHaveTextContent('Cloud');
        expect(within(within(dialog).getByRole('group', { name: 'AMD LLM Gateway' })).queryByText(/Hybrid/)).not.toBeInTheDocument();
        expect(within(dialog).getAllByRole('option', { name: /Hybrid/ })).toHaveLength(1);
    });

    it('names a pair from other providers and asks consent naming each cloud provider', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({ cheap_model: 'fireworks.deepseek-v4p1-flash' }));
        const dialog = await openPicker();
        const group = await within(dialog).findByRole('group', { name: 'Hybrid' });
        fireEvent.click(within(group).getByRole('option', { name: /Hybrid \(DeepSeek Fireworks \+ Opus\)/ }));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(within(group).getByRole('alert')).toHaveTextContent('Hybrid sends chat history to Fireworks AI, AMD LLM Gateway');
        fireEvent.click(within(group).getByRole('button', { name: /^Use Hybrid/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('hybrid'));
        await waitFor(() => expect(useModelStore.getState().active?.model).toBe('hybrid'));
    });

    it('is labelled local, with no cloud consent, when every member runs on this PC', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({ cheap_model: GEMMA, strong_model: QWEN }));
        const dialog = await openPicker();
        const row = await within(dialog).findByRole('option', { name: /Hybrid \(Gemma 4 E4B \+ Qwen3.6 35B\)/ });
        expect(row).toHaveTextContent('Local');
        fireEvent.click(row);
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('hybrid'));
    });

    it('counts a cloud classifier as cloud even when both routed models are local', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({ cheap_model: GEMMA, strong_model: QWEN, classifier_model: 'amd.DeepSeek-V4.1-Flash' }));
        const dialog = await openPicker();
        expect(await within(dialog).findByRole('option', { name: /Hybrid/ })).toHaveTextContent('Cloud');
    });

    it('is hidden when neither the configured pair nor any template can run', async () => {
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({
            models: [{ id: 'amd.DeepSeek-V4.1-Flash', display_name: 'DeepSeek', provider: 'amd', available: false, reason: 'Not deployed' }],
        }));
        mockedApi.getHybridTemplates.mockResolvedValue([{ ...TEMPLATE, available: false, reason: 'Connect the gateway' }]);
        const dialog = await openPicker();
        await waitFor(() => expect(mockedApi.getHybridTemplates).toHaveBeenCalled());
        expect(within(dialog).queryByRole('group', { name: 'Hybrid' })).not.toBeInTheDocument();
    });

    it('says why when the hybrid settings cannot be read, and makes no place claim', async () => {
        mockedApi.getHybridSettings.mockRejectedValue(new Error('hybrid_rules is required when hybrid_policy is custom.'));
        const dialog = await openPicker();
        const group = await within(dialog).findByRole('group', { name: 'Hybrid' });
        expect(group).toHaveTextContent('hybrid_rules is required when hybrid_policy is custom.');
        expect(within(group).queryByRole('option')).not.toBeInTheDocument();
    });

    it('lists hybrid first while a hybrid chat is in use', async () => {
        useModelStore.setState({ active: HYBRID_ACTIVE });
        const dialog = await openPicker();
        await within(dialog).findByRole('group', { name: 'Hybrid' });
        expect(within(dialog).getAllByRole('group')[0]).toHaveAccessibleName('Hybrid');
    });
});

describe('ModelSettings: hybrid', () => {
    it('opens on the tab of hybrid’s strong model, not Local, with the Hybrid panel open', async () => {
        useModelStore.setState({ active: HYBRID_ACTIVE });
        render(<ModelSettings />);
        await waitFor(() => expect(screen.getByRole('radio', { name: /AMD LLM Gateway/ })).toHaveAttribute('aria-checked', 'true'));
        expect(screen.getByRole('radio', { name: /Local/ })).toHaveAttribute('aria-checked', 'false');
        expect(await screen.findByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
    });

    it('stays on Local for an all-local hybrid', async () => {
        useModelStore.setState({ active: HYBRID_ACTIVE });
        mockedApi.getHybridSettings.mockResolvedValue(hybridSettings({ cheap_model: GEMMA, strong_model: QWEN }));
        render(<ModelSettings />);
        await screen.findByRole('form', { name: 'Hybrid' });
        expect(screen.getByRole('radio', { name: /Local/ })).toHaveAttribute('aria-checked', 'true');
    });

    it('reads nothing hybrid until its section is opened, when another model is in use', async () => {
        render(<ModelSettings />);
        await screen.findByRole('radio', { name: /AMD LLM Gateway/ });
        expect(mockedApi.getHybridSettings).not.toHaveBeenCalled();
        fireEvent.click(screen.getByText('Each request runs on a cheap model or a strong one'));
        expect(await screen.findByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
    });

    it('maps the in-use model to its tab; hybrid has none of its own', () => {
        expect(providerTabOf(HYBRID_ACTIVE)).toBeUndefined();
        expect(providerTabOf({ ...HYBRID_ACTIVE, model: ' Hybrid ' })).toBeUndefined();
        expect(providerTabOf(LOCAL_ACTIVE)).toBe('local');
        expect(providerTabOf(null)).toBeUndefined();
    });
});
