// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings › Run mode: both modes, what serves each capability, customize and reset. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RunModeSettings } from '../RunModeSettings';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import { runModesView } from '../../../test/runModes';
import type { ActiveModel, ProviderInfo, ProviderModel } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const DEEPSEEK = 'fireworks.deepseek-v4p1-flash';
const GLM = 'fireworks.glm-5p3-flash';

const ACTIVE: ActiveModel = { provider: 'local', model: GEMMA, label: 'Gemma', remote: false, is_default: true, downloaded: true };
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const FIREWORKS: ProviderInfo = {
    id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true, privacy_notice: 'Sent to Fireworks AI.',
};

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return { id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, ...over };
}

function serve(fireworks: ProviderInfo = FIREWORKS, local: ProviderModel[] = [model(GEMMA), model(QWEN, { tight: true })]) {
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks], active: ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async (id) => ({
        provider: id,
        models: id === 'local' ? local : [model(DEEPSEEK, { rank: 1 }), model(GLM, { rank: 2 })],
    }));
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: ACTIVE, error: null, restoreNotice: null, queued: null, names: {} });
    useChatStore.setState({ settingsSection: 'mode', settingsProvider: null, settingsJump: null, isStreaming: false });
    serve();
    mockedApi.getRunModes.mockResolvedValue(runModesView('local', { local: GEMMA, cloud: DEEPSEEK }));
    mockedApi.getActiveModel.mockResolvedValue(ACTIVE);
    mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });
});

const card = async (name: 'Local' | 'Cloud') => screen.findByRole('region', { name: new RegExp(`^${name}`) });

describe('RunModeSettings', () => {
    it('shows both modes, marks the one in use, and says what serves each capability', async () => {
        render(<RunModeSettings />);
        const local = await card('Local');
        const cloud = await card('Cloud');
        expect(within(local).getByText('In use')).toBeInTheDocument();
        expect(within(cloud).queryByText('In use')).not.toBeInTheDocument();
        expect(within(local).getByText('Everything runs on this PC.')).toBeInTheDocument();
        expect(within(cloud).getByText('Chat runs on Fireworks AI. Everything else stays on this PC.')).toBeInTheDocument();

        for (const label of ['Reading images', 'Transcribing audio', 'Reading aloud', 'Creating images', 'Documents and memory']) {
            expect(within(local).getByText(label)).toBeInTheDocument();
        }
        // Cloud says why each of these stays here, and never claims Fireworks runs it.
        expect(within(cloud).getAllByText("Fireworks AI doesn't offer this, so it stays on this PC.")).toHaveLength(3);
        expect(within(cloud).getAllByText("GAIA hasn't tested this on Fireworks AI yet, so it stays on this PC.")).toHaveLength(2);
        expect(within(local).queryByText(/stays on this PC/)).not.toBeInTheDocument();
    });

    it('offers only models the mode can run: downloaded ones for Local, the provider’s for Cloud', async () => {
        serve(FIREWORKS, [model(GEMMA), model(QWEN, { downloaded: false }), model('NPU-FLM', { available: false })]);
        render(<RunModeSettings />);
        const local = within(await card('Local')).getByLabelText('Chat') as HTMLSelectElement;
        expect(Array.from(local.options).map((o) => o.value)).toEqual([GEMMA]);
        const cloud = within(await card('Cloud')).getByLabelText('Chat') as HTMLSelectElement;
        expect(Array.from(cloud.options).map((o) => o.value)).toEqual([DEEPSEEK, GLM]);
        expect(cloud.options[0].textContent).toContain('(recommended)');
    });

    it('switches mode with Use Cloud and re-reads the model everywhere', async () => {
        mockedApi.selectRunMode.mockResolvedValue(runModesView('cloud', { local: GEMMA, cloud: DEEPSEEK }));
        render(<RunModeSettings />);
        fireEvent.click(within(await card('Cloud')).getByRole('button', { name: 'Use Cloud' }));
        await waitFor(() => expect(mockedApi.selectRunMode).toHaveBeenCalledWith('cloud'));
        await waitFor(() => expect(mockedApi.getActiveModel).toHaveBeenCalled());
        expect(within(await card('Cloud')).getByText('In use')).toBeInTheDocument();
    });

    it('saves a new chat model for a mode', async () => {
        mockedApi.updateRunMode.mockResolvedValue(
            runModesView('local', { local: GEMMA, cloud: GLM }, { cloud: { customized: true, default_display_name: 'deepseek-v4p1-flash' } }),
        );
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        fireEvent.change(within(cloud).getByLabelText('Chat'), { target: { value: GLM } });
        await waitFor(() => expect(mockedApi.updateRunMode).toHaveBeenCalledWith('cloud', GLM));
        await waitFor(() => expect((within(cloud).getByLabelText('Chat') as HTMLSelectElement).value).toBe(GLM));
    });

    it('enables Reset to defaults only for a customized mode, and resets it', async () => {
        mockedApi.getRunModes.mockResolvedValue(
            runModesView('local', { local: GEMMA, cloud: GLM }, { cloud: { customized: true, default_display_name: 'deepseek-v4p1-flash' } }),
        );
        mockedApi.resetRunMode.mockResolvedValue(runModesView('local', { local: GEMMA, cloud: DEEPSEEK }));
        render(<RunModeSettings />);
        const localReset = within(await card('Local')).getByRole('button', { name: 'Reset to defaults' });
        expect(localReset).toBeDisabled();
        expect(localReset).toHaveAttribute('title', 'Already on its defaults');
        const cloudReset = within(await card('Cloud')).getByRole('button', { name: 'Reset to defaults' });
        expect(cloudReset).toBeEnabled();
        expect(cloudReset).toHaveAttribute('title', 'Go back to deepseek-v4p1-flash');

        fireEvent.click(cloudReset);
        await waitFor(() => expect(mockedApi.resetRunMode).toHaveBeenCalledWith('cloud'));
        await waitFor(() => expect(within(screen.getByRole('region', { name: /^Cloud/ })).getByRole('button', { name: 'Reset to defaults' })).toBeDisabled());
    });

    it('asks for a key before Cloud can be used, and opens the key field', async () => {
        serve({ ...FIREWORKS, models_discovered: false });
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        expect(within(cloud).getByText('Cloud needs a Fireworks AI key before it can be used.')).toBeInTheDocument();
        expect(within(cloud).getByRole('button', { name: 'Use Cloud' })).toBeDisabled();
        expect(within(cloud).queryByLabelText('Chat', { selector: 'select' })).not.toBeInTheDocument();

        fireEvent.click(within(cloud).getByRole('button', { name: 'Add your key' }));
        const state = useChatStore.getState();
        expect(state.settingsSection).toBe('model');
        expect(state.settingsProvider).toBe('fireworks');
        expect(state.settingsJump?.target).toBe('[data-settings-target="cloud-key"]');
    });

    it('says a saved key that found no models is not working, and opens the key field', async () => {
        serve({ ...FIREWORKS, models_discovered: false, key_source: 'environment' });
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        expect(within(cloud).getByText(/has a Fireworks AI key, but it found no models/)).toBeInTheDocument();
        expect(within(cloud).getByRole('button', { name: 'Use Cloud' })).toBeDisabled();
        fireEvent.click(within(cloud).getByRole('button', { name: 'Check your key' }));
        expect(useChatStore.getState().settingsJump?.target).toBe('[data-settings-target="cloud-key"]');
    });

    it('says Local only blocks Cloud and points at Privacy', async () => {
        serve({ ...FIREWORKS, models_discovered: false, blocked: true });
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        expect(within(cloud).getByText(/Blocked by Local only/)).toBeInTheDocument();
        expect(within(cloud).getByRole('button', { name: 'Use Cloud' })).toBeDisabled();
        fireEvent.click(within(cloud).getByRole('button', { name: 'Open Privacy' }));
        expect(useChatStore.getState().settingsSection).toBe('privacy');
    });

    it('says Local cannot be used until its model is downloaded', async () => {
        useModelStore.setState({ active: { ...ACTIVE, provider: 'fireworks', model: DEEPSEEK, remote: true } });
        serve(FIREWORKS, [model(QWEN, { downloaded: false })]);
        mockedApi.getRunModes.mockResolvedValue(runModesView('cloud', { local: QWEN, cloud: DEEPSEEK }));
        render(<RunModeSettings />);
        const local = await card('Local');
        expect(within(local).getByText(`${QWEN} isn't downloaded yet, so Local can't be used.`)).toBeInTheDocument();
        expect(within(local).getByRole('button', { name: 'Use Local' })).toBeDisabled();
        fireEvent.click(within(local).getByRole('button', { name: 'Download it' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(useChatStore.getState().settingsProvider).toBe('local');
    });

    it('shows a refused change next to the mode it was for, and changes nothing', async () => {
        mockedApi.selectRunMode.mockRejectedValue(new Error("Can't switch: Fireworks AI has no working key."));
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        fireEvent.click(within(cloud).getByRole('button', { name: 'Use Cloud' }));
        expect(await within(cloud).findByRole('alert')).toHaveTextContent('Fireworks AI has no working key');
        expect(within(await card('Local')).getByText('In use')).toBeInTheDocument();
    });

    it('holds mode changes while a reply is being written', async () => {
        useChatStore.setState({ isStreaming: true });
        render(<RunModeSettings />);
        const cloud = await card('Cloud');
        expect(screen.getByText('A reply is being written. You can change modes when it finishes.')).toBeInTheDocument();
        expect(within(cloud).getByRole('button', { name: 'Use Cloud' })).toBeDisabled();
        expect(within(await card('Local')).getByLabelText('Chat')).toBeDisabled();
    });

    it('says when a capability is off on this PC instead of naming a model', async () => {
        const view = runModesView('local', { local: GEMMA, cloud: DEEPSEEK });
        for (const m of view.modes) {
            m.capabilities = m.capabilities.map((c) => (c.id === 'read_aloud' ? { ...c, model: null, display_name: null, unavailable: 'platform' } : c));
        }
        mockedApi.getRunModes.mockResolvedValue(view);
        render(<RunModeSettings />);
        const local = await card('Local');
        expect(within(local).getByText('Not offered on this operating system yet')).toBeInTheDocument();
        expect(within(local).queryByText(/kokoro/)).not.toBeInTheDocument();
    });

    it('links a capability to the setting that changes it', async () => {
        render(<RunModeSettings />);
        const local = await card('Local');
        const changes = within(local).getAllByRole('button', { name: 'Change' });
        expect(changes).toHaveLength(2);
        fireEvent.click(changes[1]);
        expect(useChatStore.getState().settingsSection).toBe('images');
    });

    it('offers no Change for a capability that is off on this PC', async () => {
        const view = runModesView('local', { local: GEMMA, cloud: DEEPSEEK });
        view.modes[0].capabilities = view.modes[0].capabilities.map((c) => (
            c.id === 'read_aloud' ? { ...c, model: null, display_name: null, unavailable: 'platform' } : c));
        mockedApi.getRunModes.mockResolvedValue(view);
        render(<RunModeSettings />);
        expect(within(await card('Local')).getAllByRole('button', { name: 'Change' })).toHaveLength(1);
    });

    it('offers Try again when the modes cannot be loaded', async () => {
        mockedApi.getRunModes.mockRejectedValueOnce(new Error('backend restarting'));
        render(<RunModeSettings />);
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load the run modes: backend restarting");
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await card('Local')).toBeInTheDocument();
    });
});
