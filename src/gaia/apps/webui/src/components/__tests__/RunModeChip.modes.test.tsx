// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Switching between Local and Cloud from the composer, and what stands in the way. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RunModeChip } from '../RunModeChip';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { runModesView } from '../../test/runModes';
import type { ActiveModel, ProviderInfo, ProviderModel, Session } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const KIMI = 'fireworks.accounts/fireworks/models/kimi-k2';

const LOCAL_ACTIVE: ActiveModel = { provider: 'local', model: GEMMA, label: 'Gemma', remote: false, is_default: true, downloaded: true };
const CLOUD_ACTIVE: ActiveModel = {
    provider: 'fireworks', model: KIMI, label: 'Kimi', remote: true, is_default: false, provider_name: 'Fireworks AI',
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };
const FIREWORKS: ProviderInfo = {
    id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true,
    privacy_notice: 'Chat history is sent to Fireworks AI.',
};
const SESSION: Session = {
    id: 's1', title: 'Chat', created_at: '', updated_at: '', model: GEMMA,
    system_prompt: null, message_count: 0, document_ids: [], inference_remote: false,
};

function model(id: string, over: Partial<ProviderModel> = {}): ProviderModel {
    return { id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, ...over };
}

function serve(local: ProviderModel[], cloud: ProviderModel[], fireworks: ProviderInfo = FIREWORKS) {
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks], active: LOCAL_ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async (id) => ({ provider: id, models: id === 'local' ? local : cloud }));
}

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({ active: LOCAL_ACTIVE, error: null, restoreNotice: null, queued: null, queueError: null, names: {} });
    useChatStore.setState({ settingsSection: null, settingsProvider: null, sessions: [SESSION], currentSessionId: SESSION.id, isStreaming: false });
    serve([model(GEMMA)], [model(KIMI, { rank: 1 })]);
    mockedApi.getRunModes.mockResolvedValue(runModesView('local', { local: GEMMA, cloud: 'fireworks.kimi-k2' }));
    mockedApi.selectModel.mockResolvedValue(CLOUD_ACTIVE);
    mockedApi.listSessions.mockResolvedValue({ sessions: [SESSION], total: 1 });
});

/** Open the menu as a user sees it first: two modes, their model lists closed. */
async function openMenu() {
    fireEvent.click(screen.getByRole('button', { name: /^(Local|Cloud)/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose where GAIA runs' });
    await within(dialog).findByRole('option', { name: /^Cloud/ });
    return dialog;
}
const mode = (dialog: HTMLElement, name: RegExp) => within(dialog).getByRole('option', { name });

describe('RunModeChip modes', () => {
    it('opens on two choices that say where things run and which model each uses', async () => {
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(within(dialog).getAllByRole('option')).toHaveLength(2);
        expect(mode(dialog, /^Local/)).toHaveTextContent('Everything runs on this PC.');
        expect(mode(dialog, /^Local/)).toHaveTextContent(GEMMA);
        expect(mode(dialog, /^Local/)).toHaveAttribute('aria-selected', 'true');
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('Chat runs in the cloud. Everything else stays on this PC.');
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('kimi-k2');
    });

    it('keeps the note about unavailable models with the Local list it belongs to', async () => {
        serve([model(GEMMA), model('NPU-FLM', { available: false })], [model(KIMI, { rank: 1 })]);
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(within(dialog).queryByText(/isn’t available on this PC/)).not.toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Show Local models' }));
        expect(within(dialog).getByText(/1 model isn’t available on this PC/)).toBeInTheDocument();
    });

    it('switches to Cloud on its saved model, named as the provider lists it, after the cloud notice', async () => {
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Cloud/));

        // Sending chat history off this PC is asked about once, never assumed.
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Chat history is sent to Fireworks AI.');
        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));

        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith(KIMI));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByRole('button', { name: 'Cloud' })).toBeInTheDocument();
    });

    it('switches back to Local without a notice', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        mockedApi.getRunModes.mockResolvedValue(runModesView('cloud', { local: GEMMA, cloud: KIMI }));
        mockedApi.selectModel.mockResolvedValue(LOCAL_ACTIVE);
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Local/));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith(GEMMA));
    });

    it('opens the list of the mode in use instead of switching', async () => {
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Local/));
        expect(await within(dialog).findByRole('option', { name: new RegExp(`^${GEMMA}`) })).toBeInTheDocument();
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('picking a model under the other mode sets it and switches in one go', async () => {
        serve([model(GEMMA)], [model(KIMI, { rank: 1 }), model('fireworks.glm-5p3-flash', { rank: 2 })]);
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Show Cloud models' }));
        fireEvent.click(await within(dialog).findByRole('option', { name: /^glm-5p3-flash/ }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Use glm-5p3-flash' }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.glm-5p3-flash'));
    });

    it('says Cloud needs a key, and opens the way to add one instead of switching', async () => {
        serve([model(GEMMA)], [], { ...FIREWORKS, models_discovered: false });
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('Needs a Fireworks AI key');

        fireEvent.click(mode(dialog, /^Cloud/));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Add your key/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(useChatStore.getState().settingsProvider).toBe('fireworks');
    });

    it('says a saved key that found no models is not working, rather than asking for a key', async () => {
        serve([model(GEMMA)], [], { ...FIREWORKS, models_discovered: false, key_source: 'stored' });
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('The Fireworks AI key found no models. Check or replace it.');
        fireEvent.click(mode(dialog, /^Cloud/));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('says Local only blocks Cloud, and does not switch', async () => {
        serve([model(GEMMA)], [], { ...FIREWORKS, models_discovered: false, blocked: true });
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('Blocked by Local only.');
        fireEvent.click(mode(dialog, /^Cloud/));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('shows why Cloud cannot be reached instead of an empty list', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => {
            if (id === 'local') return { provider: id, models: [model(GEMMA)] };
            throw new Error('Fireworks AI could not be reached. Check your internet connection.');
        });
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Cloud/)).toHaveTextContent('Fireworks AI could not be reached. Check your internet connection.');
        fireEvent.click(mode(dialog, /^Cloud/));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('asks before downloading when Local’s model is not on this PC, and never switches first', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        serve([model(QWEN, { downloaded: false, size_gb: 21.7 })], [model(KIMI, { rank: 1 })]);
        mockedApi.getRunModes.mockResolvedValue(runModesView('cloud', { local: QWEN, cloud: KIMI }));
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Local/)).toHaveTextContent(`${QWEN} isn't downloaded yet`);

        fireEvent.click(mode(dialog, /^Local/));
        expect(await within(dialog).findByRole('alert')).toHaveTextContent(`Download ${QWEN} (21.7 GB)?`);
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(mockedApi.downloadModel).not.toHaveBeenCalled();
    });

    it('warns when Local’s model is tight on memory', async () => {
        serve([model(GEMMA, { tight: true })], [model(KIMI, { rank: 1 })]);
        render(<RunModeChip />);
        const dialog = await openMenu();
        expect(mode(dialog, /^Local/)).toHaveTextContent(`${GEMMA} · tight on memory`);
    });

    it('says so when a mode’s saved model is no longer offered', async () => {
        mockedApi.getRunModes.mockResolvedValue(runModesView('local', { local: GEMMA, cloud: 'fireworks.retired-model' }));
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Cloud/));
        expect(await within(dialog).findByRole('alert')).toHaveTextContent(
            "retired-model isn't offered right now, so Cloud can't use it. Pick another model below.",
        );
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('queues a mode switch made while a reply is being written', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        useChatStore.setState({ isStreaming: true });
        mockedApi.getRunModes.mockResolvedValue(runModesView('cloud', { local: GEMMA, cloud: KIMI }));
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Local/));

        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(useModelStore.getState().queued).toBe(GEMMA);
        // The chip names the mode that comes next, not a model id.
        expect(screen.getByRole('button', { name: /^Cloud\s*→ Local next/ })).toBeInTheDocument();
    });

    it('shows a refused switch and stays in the mode it was in', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error("Can't switch to kimi-k2 · Fireworks AI: Fireworks AI has no working key."));
        render(<RunModeChip />);
        const dialog = await openMenu();
        fireEvent.click(mode(dialog, /^Cloud/));
        fireEvent.click(await within(dialog).findByRole('button', { name: 'Use kimi-k2' }));
        await waitFor(() => expect(within(dialog).getAllByRole('alert').some((a) => /has no working key/.test(a.textContent ?? ''))).toBe(true));
        expect(screen.getByRole('button', { name: 'Local' })).toBeInTheDocument();
        expect(useModelStore.getState().active).toEqual(LOCAL_ACTIVE);
    });
});
