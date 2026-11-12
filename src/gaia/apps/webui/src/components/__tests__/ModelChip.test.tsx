// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    provider_name: 'Fireworks AI',
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
    useChatStore.setState({ settingsSection: null, settingsProvider: null, sessions: [SESSION], currentSessionId: SESSION.id });
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
    fireEvent.click(screen.getByRole('button', { name: /Gemma|kimi|auto|Model/ }));
    return screen.findByRole('dialog', { name: 'Choose a model' });
}

/** Says yes to the download the list asks about. */
function confirmDownload(dialog: HTMLElement) {
    fireEvent.click(within(dialog).getByRole('button', { name: 'Download' }));
}

/** The model's row in the list, not its download icon. */
async function rowFor(dialog: HTMLElement, name: RegExp) {
    await within(dialog).findAllByRole('button', { name });
    const row = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button.popover-item'))
        .find((b) => name.test(b.textContent ?? '') || name.test(b.title));
    if (!row) throw new Error(`no model row matching ${name}`);
    return row;
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

    it('collapses unranked Fireworks variants until the user asks to see all', async () => {
        const fireworks: ProviderInfo = { ...FIREWORKS, models_discovered: true };
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks], active: LOCAL_ACTIVE, lemonade_error: null });
        mockedApi.listProviderModels.mockImplementation(async (id) => (id === 'fireworks'
            ? {
                provider: 'fireworks',
                models: [
                    model('fireworks.recommended', { rank: 1, note: 'best overall' }),
                    model('fireworks.auto'),
                    model('fireworks.spot'),
                ],
            }
            : { provider: 'local', models: [model('Gemma-4-E4B-it-GGUF')] }));
        render(<ModelChip />);
        const dialog = await openPicker();

        expect(within(dialog).getByRole('option', { name: /recommended/ })).toBeInTheDocument();
        expect(within(dialog).queryByRole('option', { name: /fireworks\.auto/ })).not.toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: 'Show all models' })).toBeInTheDocument();

        fireEvent.click(within(dialog).getByRole('option', { name: 'Show all models' }));
        expect(await within(dialog).findByRole('option', { name: /^auto$/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /^spot$/ })).toBeInTheDocument();
    });

    it('keeps the active unranked Fireworks model visible while collapsed', async () => {
        const activeCloud = { ...CLOUD_ACTIVE, model: 'fireworks.auto' };
        useModelStore.setState({ active: activeCloud });
        const fireworks: ProviderInfo = { ...FIREWORKS, models_discovered: true };
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, fireworks], active: activeCloud, lemonade_error: null });
        mockedApi.listProviderModels.mockImplementation(async (id) => (id === 'fireworks'
            ? {
                provider: 'fireworks',
                models: [model('fireworks.recommended', { rank: 1 }), model('fireworks.auto'), model('fireworks.other')],
            }
            : { provider: 'local', models: [model('Gemma-4-E4B-it-GGUF')] }));
        render(<ModelChip />);
        const dialog = await openPicker();

        expect(within(dialog).getByRole('option', { name: /^recommended$/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /^auto$/ })).toBeInTheDocument();
        expect(within(dialog).queryByRole('option', { name: /^other$/ })).not.toBeInTheDocument();
    });

    it('shows the model name in the tooltip without Markdown backticks', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, inference_remote: false, inference_description: 'Runs here, model `Qwen3`.' }],
        });
        render(<ModelChip />);
        const chip = screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF/ });
        expect(chip.getAttribute('title')).toBe('Runs here, model Qwen3.');
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
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Qwen3-8B-GGUF/ }));

        expect(await screen.findByRole('button', { name: /Qwen3-8B-GGUF · Local/ })).toBeInTheDocument();
        expect(mockedApi.listSessions).toHaveBeenCalledTimes(1);
    });

    it('still switches when the chat list cannot be refreshed', async () => {
        mockedApi.listSessions.mockRejectedValue(new Error('backend restarting'));
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^kimi-k2/ }));
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
        expect(within(dialog).getByRole('option', { name: /^kimi-k2/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ })).toHaveAttribute('aria-selected', 'true');
        expect(within(dialog).getByRole('button', { name: 'Download Qwen3-8B-GGUF' })).toBeInTheDocument();
    });

    it('asks before moving a local chat to a cloud model, then switches', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^kimi-k2/ }));

        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Chat history is sent to Fireworks AI.');

        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.accounts/fireworks/models/kimi-k2'));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByRole('button', { name: /kimi-k2 · Fireworks AI/ })).toBeInTheDocument();
    });

    it('shows the cloud notice under the clicked row and scrolls it into view', async () => {
        const scrolled = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
        render(<ModelChip />);
        const dialog = await openPicker();
        const row = await within(dialog).findByRole('option', { name: /^kimi-k2/ });
        fireEvent.click(row);

        const notice = within(dialog).getByRole('alert');
        expect(row.closest('.popover-row')?.nextElementSibling).toBe(notice);
        expect(scrolled.mock.contexts).toContain(notice);
        scrolled.mockRestore();
    });

    it('switches between local models without a notice', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id, models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF')] : [],
        }));
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Qwen3-8B-GGUF/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('picking a model that is not downloaded downloads it, then switches to it', async () => {
        let downloaded = false;
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', { downloaded })] : [],
        }));
        mockedApi.downloadModel.mockImplementation(async () => { downloaded = true; return {} as never; });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('keeps the pick while the download starts, and when the list lags its finish', async () => {
        // As the server does it: 202 before any progress, then progress, then
        // "complete" one poll before the list says downloaded.
        const steps: Partial<ProviderModel>[] = [
            { downloaded: false },
            { downloaded: false },
            { downloaded: false, download_progress: { state: 'downloading', percent: 40 } as never },
            { downloaded: false, download_progress: { state: 'complete', percent: 100 } as never },
        ];
        let calls = 0;
        mockedApi.listProviderModels.mockImplementation(async (id) => {
            if (id !== 'local') return { provider: id, models: [] };
            const qwen = steps[Math.min(calls++, steps.length - 1)];
            return { provider: id, models: [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', qwen)] };
        });
        mockedApi.downloadModel.mockResolvedValue({ status: 'downloading', model: 'Qwen3-8B-GGUF' });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'), { timeout: 9000 });
    }, 12000);

    it('never starts a download without a yes, and says how big it is', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', { downloaded: false, size_gb: 5.2 })] : [],
        }));
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Download Qwen3-8B-GGUF (5.2 GB)? GAIA switches to it when the download finishes.');
        expect(mockedApi.downloadModel).not.toHaveBeenCalled();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
        expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();

        fireEvent.click(within(dialog).getByRole('button', { name: 'Download Qwen3-8B-GGUF' }));
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Download Qwen3-8B-GGUF (5.2 GB)?');
        expect(within(dialog).getByRole('alert')).not.toHaveTextContent('switches');
        expect(mockedApi.downloadModel).not.toHaveBeenCalled();
        confirmDownload(dialog);
        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('keeps one question open: a download question replaces the cloud one', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^kimi-k2/ }));
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Chat history is sent to Fireworks AI.');

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        expect(within(dialog).getAllByRole('alert')).toHaveLength(1);
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Download Qwen3-8B-GGUF');

        fireEvent.click(within(dialog).getByRole('option', { name: /^kimi-k2/ }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Download Qwen3-8B-GGUF' }));
        expect(within(dialog).getAllByRole('alert')).toHaveLength(1);
        expect(mockedApi.downloadModel).not.toHaveBeenCalled();
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('lists the provider in use first, and models on this PC before ones to download', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Qwen3-8B-GGUF', { downloaded: false, size_gb: 5.2 }), model('Gemma-4-E4B-it-GGUF', { size_gb: 5.6 })]
                : [model('fireworks.accounts/fireworks/models/kimi-k2', { rank: 1 })],
        }));
        render(<ModelChip />);
        const dialog = await openPicker();
        await within(dialog).findByRole('option', { name: /^kimi-k2/ });

        const headings = Array.from(dialog.querySelectorAll('.popover-heading')).map((h) => h.textContent);
        expect(headings).toEqual(['Fireworks AI', 'Local']);
        const rows = within(dialog).getAllByRole('option').map((o) => o.textContent);
        expect(rows).toEqual(['kimi-k2', 'Gemma-4-E4B-it-GGUF5.6 GB', 'Qwen3-8B-GGUF5.2 GB']);
        const subheading = dialog.querySelector('.popover-subheading')!;
        expect(subheading).toHaveTextContent('Available to download');
        expect(subheading.compareDocumentPosition(within(dialog).getByRole('option', { name: /^Qwen3/ })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('a download the server reports as failed drops the pick and says why', async () => {
        let qwen: Partial<ProviderModel> = { downloaded: false };
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', qwen)] : [],
        }));
        mockedApi.downloadModel.mockImplementation(async () => {
            qwen = { downloaded: false, download_progress: { state: 'error', percent: 0, message: 'No space left on device' } as never };
            return { status: 'downloading', model: 'Qwen3-8B-GGUF' };
        });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Downloading Qwen3-8B-GGUF failed: No space left on device/);
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('a download that never starts is given up, visibly', async () => {
        mockedApi.downloadModel.mockResolvedValue({ status: 'downloading', model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByRole('alert', {}, { timeout: 13000 }))
            .toHaveTextContent(/download of Qwen3-8B-GGUF never started/);
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    }, 16000);

    it('a model picked while another downloads is not undone when the download finishes', async () => {
        let qwen: Partial<ProviderModel> = { downloaded: false };
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF'), model('Phi-4-GGUF'), model('Qwen3-8B-GGUF', qwen)]
                : [],
        }));
        mockedApi.downloadModel.mockImplementation(async () => {
            qwen = { downloaded: false, download_progress: { state: 'downloading', percent: 40 } as never };
            return {} as never;
        });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Phi-4-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);
        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        // While Qwen downloads, the user picks Phi, which is already here.
        fireEvent.click(await rowFor(dialog, /Phi/));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Phi-4-GGUF'));

        qwen = { downloaded: true };
        await new Promise((r) => setTimeout(r, 2300)); // one download poll
        expect(mockedApi.selectModel).not.toHaveBeenCalledWith('Qwen3-8B-GGUF');
    }, 10000);

    it('a pick whose model drops out of the list is given up, visibly', async () => {
        let listed = true;
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF'), ...(listed ? [model('Qwen3-8B-GGUF', { downloaded: false })] : [])]
                : [],
        }));
        mockedApi.downloadModel.mockImplementation(async () => { listed = false; return {} as never; });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByRole('alert', {}, { timeout: 13000 }))
            .toHaveTextContent(/Qwen3-8B-GGUF is no longer in the model list/);
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    }, 16000);

    it("a failed download of another model keeps the pick waiting on its own download", async () => {
        let qwen: Partial<ProviderModel> = { downloaded: false };
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF'), model('Phi-4-GGUF', { downloaded: false }), model('Qwen3-8B-GGUF', qwen)]
                : [],
        }));
        mockedApi.downloadModel.mockImplementation(async (id) => {
            if (id === 'Phi-4-GGUF') throw new Error('Disk full');
            qwen = { downloaded: false, download_progress: { state: 'downloading', percent: 40 } as never };
            return {} as never;
        });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);
        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        const phi = await within(dialog).findByRole('button', { name: 'Download Phi-4-GGUF' });
        await waitFor(() => expect(phi).not.toBeDisabled());
        fireEvent.click(phi);
        confirmDownload(dialog);
        expect(await within(dialog).findByText(/Disk full/)).toBeInTheDocument();

        qwen = { downloaded: true };
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'), { timeout: 5000 });
    }, 10000);

    it('a switch that fails after the download is shown on the chip', async () => {
        let downloaded = false;
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local' ? [model('Gemma-4-E4B-it-GGUF'), model('Qwen3-8B-GGUF', { downloaded })] : [],
        }));
        mockedApi.downloadModel.mockImplementation(async () => { downloaded = true; return {} as never; });
        mockedApi.selectModel.mockRejectedValue(new Error('Not enough memory to load it'));
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        await waitFor(() => expect(screen.getByLabelText('Model switch failed')).toBeInTheDocument());
        const chip = screen.getByLabelText('Model switch failed').closest('button')!;
        expect(chip.getAttribute('title')).toMatch(/downloaded, but switching to it failed: Not enough memory/);
    });

    it('does not switch to a model whose download failed', async () => {
        mockedApi.downloadModel.mockRejectedValue(new Error('Disk full'));
        render(<ModelChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByText(/Disk full/)).toBeInTheDocument();
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('shows why a switch failed', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error('Model failed to load'));
        useModelStore.setState({ active: CLOUD_ACTIVE });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ }));
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Model failed to load');
    });

    it('surfaces a model-server error instead of an empty list', async () => {
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL], active: LOCAL_ACTIVE, lemonade_error: 'Lemonade is not running' });
        render(<ModelChip />);
        const dialog = await openPicker();
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Lemonade is not running');
    });

    it('says when the OS is asking for keychain access, then lists the models once answered', async () => {
        const waiting: ProviderInfo = {
            ...FIREWORKS, models_discovered: false, key_source: null,
            key_state: 'waiting_for_os_permission',
            key_message: 'macOS is asking GAIA for keychain access to read your saved Fireworks AI key.',
        };
        mockedApi.listProviders
            .mockResolvedValueOnce({ providers: [LOCAL, waiting, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null })
            .mockResolvedValue({ providers: [LOCAL, FIREWORKS, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
        render(<ModelChip />);
        const dialog = await openPicker();

        const status = await within(dialog).findByText(/asking GAIA for keychain access/);
        expect(status.closest('[role="status"]')).not.toBeNull();
        expect(within(dialog).queryByText('Loading models…')).not.toBeInTheDocument();
        expect(within(dialog).queryByText('Add your key')).not.toBeInTheDocument();

        // The picker asks again on its own, so answering the dialog is enough.
        expect(await within(dialog).findByRole('button', { name: /kimi-k2/ }, { timeout: 4000 })).toBeInTheDocument();
        expect(within(dialog).queryByText(/keychain access/)).not.toBeInTheDocument();
    });

    it('offers Try again for a locked key store instead of polling or "Add your key"', async () => {
        const locked: ProviderInfo = {
            ...FIREWORKS, models_discovered: false, key_source: null,
            key_state: 'store_locked',
            key_message: 'The OS credential store is locked: its unlock prompt was closed.',
        };
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, locked, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
        mockedApi.retryKeyStore.mockResolvedValue({ retrying: true });
        render(<ModelChip />);
        const dialog = await openPicker();

        const retry = await within(dialog).findByRole('option', { name: /Try again/ });
        expect(within(dialog).getByText(/key store is locked/)).toBeInTheDocument();
        expect(within(dialog).queryByText('Add your key')).not.toBeInTheDocument();
        const reads = mockedApi.listProviders.mock.calls.length;
        await new Promise((resolve) => setTimeout(resolve, 2500));
        expect(mockedApi.listProviders.mock.calls.length).toBe(reads);

        fireEvent.click(retry);
        await waitFor(() => expect(mockedApi.retryKeyStore).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(mockedApi.listProviders.mock.calls.length).toBe(reads + 1));
    });

    it('opens model settings from the picker', async () => {
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Providers and keys…' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('"Add your key" opens settings on that provider', async () => {
        const noKey = { ...FIREWORKS, models_discovered: false };
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, noKey, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
        render(<ModelChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Add your key/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(useChatStore.getState().settingsProvider).toBe('fireworks');
    });
});

describe('ModelChip keyboard', () => {
    const KIMI = 'fireworks.accounts/fireworks/models/kimi-k2';

    beforeEach(() => {
        // A busy row sits between Gemma and Qwen; the Fireworks heading sits before kimi.
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [
                    model('Gemma-4-E4B-it-GGUF'),
                    model('Busy-GGUF', { downloaded: false, download_progress: { state: 'downloading', percent: 10 } as never }),
                    model('Qwen3-8B-GGUF'),
                ]
                : [model(KIMI)],
        }));
    });
    async function openWithKeyboard(user: ReturnType<typeof userEvent.setup>) {
        screen.getByRole('button', { name: /Gemma|kimi|Model/ }).focus();
        await user.keyboard('{Enter}');
        const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
        await within(dialog).findByRole('option', { name: /^kimi-k2/ });
        return dialog;
    }
    async function openLocalWithKeyboard(user: ReturnType<typeof userEvent.setup>) {
        screen.getByRole('button', { name: /Gemma|kimi|Model/ }).focus();
        await user.keyboard('{Enter}');
        const dialog = await screen.findByRole('dialog', { name: 'Choose a model' });
        await within(dialog).findByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ });
        return dialog;
    }
    const option = (dialog: HTMLElement, name: RegExp) => within(dialog).getByRole('option', { name });

    it('opens on the current model, scrolled into view, as the only Tab stop', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);

        const kimi = option(dialog, /^kimi-k2/);
        await waitFor(() => expect(kimi).toHaveFocus());
        expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
        expect(kimi).toHaveAttribute('aria-selected', 'true');
        expect(within(dialog).getByRole('listbox', { name: 'Models' })).toBeInTheDocument();
        const stops = within(dialog).getAllByRole('option').filter((o) => o.tabIndex === 0);
        expect(stops).toEqual([kimi]);
    });

    it('moves with the arrows, Home and End, skipping headings and busy rows', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^kimi-k2/)).toHaveFocus());

        // The provider in use (Fireworks) is listed first; the downloading row is skipped.
        await user.keyboard('{ArrowUp}');
        expect(option(dialog, /^kimi-k2/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
        await user.keyboard('{Home}');
        expect(option(dialog, /^kimi-k2/)).toHaveFocus();
        await user.keyboard('{End}');
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
        expect(option(dialog, /^Qwen3-8B-GGUF/).tabIndex).toBe(0);
        expect(option(dialog, /^kimi-k2/).tabIndex).toBe(-1);
    });

    it('makes the expanded Fireworks list keyboard-reachable and focuses its first new row', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF')]
                : [model('fireworks.recommended', { rank: 1 }), model('fireworks.auto')],
        }));
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openLocalWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus());

        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^recommended$/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Show all models$/)).toHaveFocus();
        await user.keyboard('{Enter}');
        await waitFor(() => expect(option(dialog, /^auto$/)).toHaveFocus());
    });

    it('picks the focused row on Enter', async () => {
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus());

        await user.keyboard('{ArrowDown}{Enter}');
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('closes on Escape and returns focus to the chip', async () => {
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus());

        await user.keyboard('{Escape}');
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Gemma-4-E4B-it-GGUF · Local/ })).toHaveFocus();
    });

    it('leaves the list in one Tab, and Shift+Tab comes back to the same row', async () => {
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus());
        await user.keyboard('{ArrowDown}');

        await user.tab();
        // The first stop after the list is the footer's first item.
        expect(within(dialog).getByRole('button', { name: /Add a model from a link/ })).toHaveFocus();
        await user.tab({ shift: true });
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
    });

    it("shows the focused row's details on ArrowRight and hides them on ArrowLeft", async () => {
        const user = userEvent.setup();
        render(<ModelChip />);
        const dialog = await openWithKeyboard(user);
        const gemma = option(dialog, /^Gemma-4-E4B-it-GGUF/);
        await waitFor(() => expect(gemma).toHaveFocus());

        await user.keyboard('{ArrowRight}');
        const bubble = await screen.findByRole('tooltip');
        expect(bubble).toHaveTextContent('Gemma-4-E4B-it-GGUF');
        expect(gemma).toHaveAttribute('aria-describedby', bubble.id);

        await user.keyboard('{ArrowLeft}');
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
        expect(gemma).not.toHaveAttribute('aria-describedby');
    });
});
