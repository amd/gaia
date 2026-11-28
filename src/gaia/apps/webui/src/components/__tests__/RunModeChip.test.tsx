// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { RunModeChip } from '../RunModeChip';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { ActiveModel, ProviderInfo, ProviderModel, Session } from '../../types';

import { openModeMenu, runModesView } from '../../test/runModes';

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
    mockedApi.getRunModes.mockResolvedValue(runModesView('local'));
});

async function openPicker() {
    return openModeMenu();
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

describe('RunModeChip', () => {
    it('says Local for a local model, and names the model on hover', () => {
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Local' });
        expect(chip.getAttribute('title')).toBe('Gemma-4-E4B-it-GGUF · Runs on this PC');
    });

    it('has a customize button that opens Settings on Run mode', () => {
        render(<RunModeChip />);
        fireEvent.click(screen.getByRole('button', { name: 'Customize run modes' }));
        expect(useChatStore.getState().settingsSection).toBe('mode');
    });

    it('does not claim Local when the backend cannot tell where chat runs', () => {
        useChatStore.setState({ sessions: [{ ...SESSION, inference_remote: null }] });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Mode' });
        expect(chip.textContent).not.toMatch(/local|cloud/i);
    });

    it('names the cloud provider the backend reports for a non-cloud pick', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, inference_remote: true, inference_provider_name: 'Fireworks AI', inference_description: 'Sent to Fireworks AI.' }],
        });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Cloud' });
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
        const dialog = await openPicker();

        expect(within(dialog).getByRole('option', { name: /^recommended$/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /^auto$/ })).toBeInTheDocument();
        expect(within(dialog).queryByRole('option', { name: /^other$/ })).not.toBeInTheDocument();
    });

    it('shows the model name in the tooltip without Markdown backticks', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, inference_remote: true, inference_provider_name: 'Fireworks AI', inference_description: 'Runs there, model `Qwen3`.' }],
        });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Cloud' });
        expect(chip.getAttribute('title')).toBe('Runs there, model Qwen3.');
    });

    it('says Cloud for a cloud model, and names the model and provider on hover', () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        useChatStore.setState({ currentSessionId: null });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Cloud' });
        expect(chip.getAttribute('title')).toBe('kimi-k2 · Runs on Fireworks AI — chat history leaves this PC');
    });

    it("names the open chat's model, not the picked one", () => {
        useChatStore.setState({ sessions: [{ ...SESSION, effective_model: 'Qwen3-30B-A3B-Instruct-2507-GGUF' }] });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: 'Local' });
        expect(chip.getAttribute('title')).toBe('Qwen3-30B-A3B-Instruct-2507-GGUF · Runs on this PC');
    });

    it('names the model the backend resolves when it differs from the stored one', () => {
        useChatStore.setState({ sessions: [{ ...SESSION, model: 'qwen', effective_model: 'my-custom-model' }] });
        render(<RunModeChip />);
        expect(screen.getByRole('button', { name: 'Local' }).getAttribute('title')).toMatch(/^my-custom-model · /);
    });

    it('names the Claude model a chat runs under the eval provider', () => {
        useChatStore.setState({
            sessions: [{
                ...SESSION, effective_model: 'claude-sonnet-4-5', inference_remote: true,
                inference_provider: 'claude', inference_provider_name: 'Anthropic',
            }],
        });
        render(<RunModeChip />);
        expect(screen.getByRole('button', { name: 'Cloud' }).getAttribute('title'))
            .toBe('claude-sonnet-4-5 · Runs on Anthropic — chat history leaves this PC');
    });

    it('names the picked model when no chat is open', () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, effective_model: 'Qwen3-30B-A3B-Instruct-2507-GGUF' }],
            currentSessionId: null,
        });
        render(<RunModeChip />);
        expect(screen.getByRole('button', { name: 'Local' }).getAttribute('title')).toMatch(/^Gemma-4-E4B-it-GGUF · /);
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
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Qwen3-8B-GGUF/ }));

        await waitFor(() => expect(screen.getByRole('button', { name: 'Local' }).getAttribute('title')).toMatch(/^Qwen3-8B-GGUF · /));
        expect(mockedApi.listSessions).toHaveBeenCalledTimes(1);
    });

    it('still switches when the chat list cannot be refreshed', async () => {
        mockedApi.listSessions.mockRejectedValue(new Error('backend restarting'));
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^kimi-k2/ }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));

        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByRole('button', { name: 'Cloud' })).toBeInTheDocument();
    });

    it('lists models from connected providers only', async () => {
        render(<RunModeChip />);
        const dialog = await openPicker();
        await within(dialog).findByText('Fireworks AI');

        expect(within(dialog).getByRole('option', { name: /^Local/ })).toHaveAttribute('aria-selected', 'true');
        expect(within(dialog).getByRole('option', { name: /^Cloud/ })).toHaveAttribute('aria-selected', 'false');
        expect(within(dialog).queryByText('AMD LLM Gateway')).not.toBeInTheDocument();
        expect(mockedApi.listProviderModels).not.toHaveBeenCalledWith('amd');
        expect(within(dialog).getByRole('option', { name: /^kimi-k2/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ })).toHaveAttribute('aria-selected', 'true');
        expect(within(dialog).getByRole('button', { name: 'Download Qwen3-8B-GGUF' })).toBeInTheDocument();
    });

    it('asks before moving a local chat to a cloud model, then switches', async () => {
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^kimi-k2/ }));

        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(within(dialog).getByRole('alert')).toHaveTextContent('Chat history is sent to Fireworks AI.');

        fireEvent.click(within(dialog).getByRole('button', { name: 'Use kimi-k2' }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.accounts/fireworks/models/kimi-k2'));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByRole('button', { name: 'Cloud' })).toBeInTheDocument();
    });

    it('shows the cloud notice under the clicked row and scrolls it into view', async () => {
        const scrolled = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        await waitFor(() => expect(mockedApi.downloadModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('switches to a picked model when its download ends with the window hidden', async () => {
        let started = false;
        let listsAfterStart = 0;
        mockedApi.listProviderModels.mockImplementation(async (id) => {
            if (id !== 'local') return { provider: id, models: [] };
            const done = started && listsAfterStart++ > 0;
            const qwen = done
                ? model('Qwen3-8B-GGUF')
                : model('Qwen3-8B-GGUF', {
                    downloaded: false,
                    download_progress: started ? { state: 'downloading', percent: 40 } as never : undefined,
                });
            return { provider: id, models: [model('Gemma-4-E4B-it-GGUF'), qwen] };
        });
        mockedApi.downloadModel.mockImplementation(async () => { started = true; return {} as never; });
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        const visibility = vi.spyOn(document, 'visibilityState', 'get');
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            render(<RunModeChip />);
            const dialog = await openPicker();
            fireEvent.click(await rowFor(dialog, /Qwen3/));
            confirmDownload(dialog);
            await waitFor(() => expect(listsAfterStart).toBe(1));
            visibility.mockReturnValue('hidden');
            document.dispatchEvent(new Event('visibilitychange'));
            await vi.advanceTimersByTimeAsync(2100);
            await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
        } finally {
            vi.useRealTimers();
            visibility.mockRestore();
        }
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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

    it('lists Local before Cloud, and models on this PC before ones to download', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Qwen3-8B-GGUF', { downloaded: false, size_gb: 5.2 }), model('Gemma-4-E4B-it-GGUF', { size_gb: 5.6 })]
                : [model('fireworks.accounts/fireworks/models/kimi-k2', { rank: 1 })],
        }));
        render(<RunModeChip />);
        const dialog = await openPicker();
        await within(dialog).findByRole('option', { name: /^kimi-k2/ });

        const headings = Array.from(dialog.querySelectorAll('.popover-heading')).map((h) => h.textContent);
        expect(headings).toEqual(['Fireworks AI']);
        const rows = within(dialog).getAllByRole('option').map((o) => o.dataset.row);
        expect(rows).toEqual([
            'mode:local', 'Gemma-4-E4B-it-GGUF', 'Qwen3-8B-GGUF',
            'mode:cloud', 'fireworks.accounts/fireworks/models/kimi-k2',
        ]);
        const subheading = dialog.querySelector('.popover-subheading')!;
        expect(subheading).toHaveTextContent('Available to download');
        expect(subheading.compareDocumentPosition(within(dialog).getByRole('option', { name: /^Qwen3/ })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('lists verified models to download first, in the order sent, each tagged as setup tags it', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [
                    model('Gemma-4-E4B-it-GGUF', { support: 'verified' }),
                    model('Qwen3.6-35B-A3B-GGUF', { downloaded: false, support: 'verified' }),
                    model('Aardvark-21B-GGUF', { downloaded: false, support: 'untested' }),
                    model('Qwen3.5-4B-GGUF', { downloaded: false, support: 'verified' }),
                    model('Mine-GGUF', { downloaded: false, support: null }),
                    model('Qwen3.5-2B-GGUF', { downloaded: false, support: 'verified' }),
                ]
                : [],
        }));
        render(<RunModeChip />);
        const dialog = await openPicker();
        await within(dialog).findByRole('option', { name: /^Aardvark/ });
        const rows = within(dialog).getAllByRole('option').filter((o) => !o.dataset.row?.startsWith('mode:'));
        expect(rows.map((r) => r.querySelector('.popover-item-title')!.textContent)).toEqual([
            'Gemma-4-E4B-it-GGUF', 'Qwen3.6-35B-A3B-GGUF', 'Qwen3.5-4B-GGUF', 'Qwen3.5-2B-GGUF', 'Aardvark-21B-GGUF', 'Mine-GGUF',
        ]);
        const tags = rows.map((r) => r.querySelector('.mc-chip')?.textContent ?? null);
        expect(tags).toEqual(['Verified', 'Verified', 'Verified', 'Verified', 'Untested', null]);
        expect(rows[4].querySelector('.mc-chip')).toHaveClass('is-untested');
    });

    it('caps the list to the room the main column leaves it, so its last row can be scrolled to', async () => {
        vi.stubGlobal('innerHeight', 800);
        const real = HTMLElement.prototype.getBoundingClientRect;
        // The main column clips what overflows it, and ends before the window does.
        const box = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
            if (this.classList.contains('main-content')) return { top: 0, bottom: 700, left: 0, right: 1000 } as DOMRect;
            if (this.classList.contains('composer-chip')) return { top: 200, bottom: 230, left: 600, right: 700 } as DOMRect;
            return real.call(this);
        });
        const tall = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(900);
        onTestFinished(() => { box.mockRestore(); tall.mockRestore(); vi.unstubAllGlobals(); });
        render(<div className="main-content" style={{ overflow: 'hidden' }}><RunModeChip /></div>);
        const dialog = await openPicker();
        await within(dialog).findByRole('option', { name: /^Qwen3/ });
        expect(dialog).toHaveClass('popover-below');
        // 700 - 230 - 14: to the column's end, not the window's 800.
        expect(dialog.style.getPropertyValue('--popover-room')).toBe('456px');
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
        render(<RunModeChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Downloading Qwen3-8B-GGUF failed: No space left on device/);
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('a download that never starts is given up, visibly', async () => {
        mockedApi.downloadModel.mockResolvedValue({ status: 'downloading', model: 'Qwen3-8B-GGUF' });
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        await waitFor(() => expect(screen.getByLabelText('Model switch failed')).toBeInTheDocument());
        const chip = screen.getByLabelText('Model switch failed').closest('button')!;
        expect(chip.getAttribute('title')).toMatch(/downloaded, but switching to it failed: Not enough memory/);
    });

    it('does not switch to a model whose download failed', async () => {
        mockedApi.downloadModel.mockRejectedValue(new Error('Disk full'));
        render(<RunModeChip />);
        const dialog = await openPicker();

        fireEvent.click(await rowFor(dialog, /Qwen3/));
        confirmDownload(dialog);

        expect(await within(dialog).findByText(/Disk full/)).toBeInTheDocument();
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('shows why a switch failed', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error('Model failed to load'));
        useModelStore.setState({ active: CLOUD_ACTIVE });
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ }));
        expect(await within(dialog).findByRole('alert')).toHaveTextContent('Model failed to load');
    });

    it('surfaces a model-server error instead of an empty list', async () => {
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL], active: LOCAL_ACTIVE, lemonade_error: 'Lemonade is not running' });
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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
        render(<RunModeChip />);
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

    it('opens Run mode settings from the menu', async () => {
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(within(dialog).getByRole('button', { name: /^Customize…/ }));
        expect(useChatStore.getState().settingsSection).toBe('mode');
    });

    it('"Add your key" opens settings on that provider', async () => {
        const noKey = { ...FIREWORKS, models_discovered: false };
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL, noKey, AMD_UNCONNECTED], active: LOCAL_ACTIVE, lemonade_error: null });
        render(<RunModeChip />);
        const dialog = await openPicker();
        fireEvent.click(await within(dialog).findByRole('option', { name: /^Add your key/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(useChatStore.getState().settingsProvider).toBe('fireworks');
    });
});

describe('RunModeChip keyboard', () => {
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
        screen.getByRole('button', { name: /^(Local|Cloud)$/ }).focus();
        await user.keyboard('{Enter}');
        const dialog = await screen.findByRole('dialog', { name: 'Choose where GAIA runs' });
        await within(dialog).findByRole('option', { name: /^Local/ });
        return dialog;
    }
    const option = (dialog: HTMLElement, name: RegExp) => within(dialog).getByRole('option', { name });

    it('opens on the mode in use as the only Tab stop, with the model lists closed', async () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);

        const cloud = option(dialog, /^Cloud/);
        await waitFor(() => expect(cloud).toHaveFocus());
        expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
        expect(cloud).toHaveAttribute('aria-selected', 'true');
        expect(within(dialog).getByRole('listbox', { name: 'Choose where GAIA runs' })).toBeInTheDocument();
        const options = within(dialog).getAllByRole('option');
        expect(options.map((o) => o.dataset.row)).toEqual(['mode:local', 'mode:cloud']);
        expect(options.filter((o) => o.tabIndex === 0)).toEqual([cloud]);
    });

    it('opens a mode on ArrowRight and closes it on ArrowLeft', async () => {
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        const local = option(dialog, /^Local/);
        await waitFor(() => expect(local).toHaveFocus());

        await user.keyboard('{ArrowRight}');
        expect(await within(dialog).findByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('button', { name: 'Show Local models' })).toHaveAttribute('aria-expanded', 'true');
        await user.keyboard('{ArrowLeft}');
        expect(within(dialog).queryByRole('option', { name: /^Gemma-4-E4B-it-GGUF/ })).not.toBeInTheDocument();
        expect(local).toHaveFocus();
    });

    it('moves with the arrows, Home and End, skipping headings and busy rows', async () => {
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());

        // Local's models open under it; the downloading row is skipped.
        await user.keyboard('{ArrowRight}{ArrowUp}');
        expect(option(dialog, /^Local/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Gemma-4-E4B-it-GGUF/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Cloud/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Cloud/)).toHaveFocus();
        await user.keyboard('{Home}');
        expect(option(dialog, /^Local/)).toHaveFocus();
        await user.keyboard('{End}');
        expect(option(dialog, /^Cloud/)).toHaveFocus();
        expect(option(dialog, /^Cloud/).tabIndex).toBe(0);
        expect(option(dialog, /^Local/).tabIndex).toBe(-1);
    });

    it('makes the expanded Fireworks list keyboard-reachable and focuses its first new row', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF')]
                : [model('fireworks.recommended', { rank: 1 }), model('fireworks.auto')],
        }));
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());

        await user.keyboard('{ArrowDown}{ArrowRight}{ArrowDown}');
        expect(option(dialog, /^recommended$/)).toHaveFocus();
        await user.keyboard('{ArrowDown}');
        expect(option(dialog, /^Show all models$/)).toHaveFocus();
        await user.keyboard('{Enter}');
        await waitFor(() => expect(option(dialog, /^auto$/)).toHaveFocus());
    });

    it('picks the focused model on Enter', async () => {
        mockedApi.selectModel.mockResolvedValue({ ...LOCAL_ACTIVE, model: 'Qwen3-8B-GGUF' });
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());

        await user.keyboard('{ArrowRight}{ArrowDown}{ArrowDown}{Enter}');
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('Qwen3-8B-GGUF'));
    });

    it('closes on Escape and returns focus to the chip', async () => {
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());

        await user.keyboard('{Escape}');
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Local' })).toHaveFocus();
    });

    it('leaves the list in one Tab, and Shift+Tab comes back to the same row', async () => {
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());
        await user.keyboard('{ArrowRight}{ArrowDown}{ArrowDown}');

        await user.tab();
        // The first stop after the list is the footer's item.
        expect(within(dialog).getByRole('button', { name: /^Customize…/ })).toHaveFocus();
        await user.tab({ shift: true });
        expect(option(dialog, /^Qwen3-8B-GGUF/)).toHaveFocus();
    });

    it("shows the focused model's details on ArrowRight and hides them on ArrowLeft", async () => {
        const user = userEvent.setup();
        render(<RunModeChip />);
        const dialog = await openWithKeyboard(user);
        await waitFor(() => expect(option(dialog, /^Local/)).toHaveFocus());
        await user.keyboard('{ArrowRight}{ArrowDown}');
        const gemma = option(dialog, /^Gemma-4-E4B-it-GGUF/);
        expect(gemma).toHaveFocus();

        await user.keyboard('{ArrowRight}');
        const bubble = await screen.findByRole('tooltip');
        expect(bubble).toHaveTextContent('Gemma-4-E4B-it-GGUF');
        expect(gemma).toHaveAttribute('aria-describedby', bubble.id);

        await user.keyboard('{ArrowLeft}');
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
        expect(gemma).not.toHaveAttribute('aria-describedby');
    });
});

describe('RunModeChip in Arabic', () => {
    // The backend's own wording, which the UI translates by provider.
    const LOCAL_SERVER: ProviderInfo = { ...LOCAL, privacy_notice: 'Runs on this PC. Nothing leaves your machine.' };
    const FIREWORKS_SERVER: ProviderInfo = { ...FIREWORKS, privacy_notice: 'Chat history is sent to Fireworks AI. Usage may incur charges.' };

    beforeEach(() => {
        useChatStore.setState({ locale: 'ar' });
        mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL_SERVER, FIREWORKS_SERVER], active: LOCAL_ACTIVE, lemonade_error: null });
    });
    afterEach(() => useChatStore.setState({ locale: 'en' }));

    async function openArabic() {
        fireEvent.click(screen.getByRole('button', { name: /محلي|سحابي/ }));
        const dialog = await screen.findByRole('dialog', { name: /اختيار مكان تشغيل GAIA/ });
        const cloud = await within(dialog).findByRole('button', { name: /عرض نماذج الوضع السحابي/ });
        fireEvent.click(cloud);
        return dialog;
    }

    it('names the modes in Arabic and keeps the cloud brand name', async () => {
        render(<RunModeChip />);
        const dialog = await openArabic();
        expect(within(dialog).getByRole('option', { name: /محلي.*\(Local\)/ })).toBeInTheDocument();
        expect(within(dialog).getByRole('option', { name: /سحابي.*\(Cloud\)/ })).toBeInTheDocument();
        const headings = Array.from(dialog.querySelectorAll('.popover-heading')).map((h) => h.textContent);
        expect(headings).toEqual(['Fireworks AI']);
    });

    it('warns in Arabic before a cloud model is used', async () => {
        render(<RunModeChip />);
        const dialog = await openArabic();
        fireEvent.click(await rowFor(dialog, /kimi-k2/));
        const alert = await within(dialog).findByRole('alert');
        expect(alert).toHaveTextContent('يُرسَل سجل المحادثة إلى Fireworks AI');
        expect(within(alert).getByRole('button', { name: /استخدام.*kimi-k2/ })).toBeInTheDocument();
    });

    it('says a cloud chat leaves this PC in Arabic on hover', () => {
        useModelStore.setState({ active: CLOUD_ACTIVE });
        useChatStore.setState({ sessions: [{ ...SESSION, inference_remote: true, inference_provider_name: 'Fireworks AI' }] });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: /سحابي/ });
        expect(chip.getAttribute('title')).toContain('ويغادر سجل المحادثة هذا الجهاز');
    });

    it('labels the not-downloaded icon in Arabic', () => {
        useModelStore.setState({ active: { ...LOCAL_ACTIVE, downloaded: false } });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: /محلي/ });
        expect(chip).toHaveClass('is-warning');
        expect(chip.querySelector('[aria-label*="غير منزَّل"]')).not.toBeNull();
        expect(chip.getAttribute('title')).toContain('لم يُنزَّل');
    });

    it('localizes the queued next-model indicator and isolates the model name LTR in Arabic', () => {
        useModelStore.setState({ active: CLOUD_ACTIVE, queued: 'fireworks.glm-5p3-flash' });
        render(<RunModeChip />);
        const chip = screen.getByRole('button', { name: /سحابي/ });
        const next = chip.querySelector('.composer-chip-next');
        expect(next).not.toBeNull();
        expect(next?.textContent).not.toContain('→');
        expect(next?.textContent).not.toContain('next');
        expect(next?.querySelector('.composer-chip-next-arrow')).not.toBeNull();
        const modelBdi = next?.querySelector('bdi[dir="ltr"].composer-chip-next-model');
        expect(modelBdi).toHaveTextContent('glm-5p3-flash');
        expect(next).toHaveTextContent('التالي');
        expect(chip.getAttribute('title')).toContain('بدءًا من رسالتك التالية');
    });

    it('localizes cloud and local model InfoBubble details and aria-labels in Arabic with LTR isolation', async () => {
        mockedApi.listProviderModels.mockImplementation(async (id) => ({
            provider: id,
            models: id === 'local'
                ? [model('Gemma-4-E4B-it-GGUF', {
                    size_gb: 6,
                    tokens_per_second: 42.4,
                    custom: true,
                    placement: { label: 'GPU · Vulkan', source: 'expected', device: 'gpu' } as never,
                })]
                : [model('fireworks.accounts/fireworks/models/kimi-k2', {
                    rank: 1,
                    note: 'best overall, fastest',
                    price: { input_per_million: 0.3, output_per_million: 1.2, cached_input_per_million: 0, source: 'test' },
                })],
        }));
        render(<RunModeChip />);
        const dialog = await openArabic();
        fireEvent.click(await within(dialog).findByRole('button', { name: /عرض نماذج الوضع المحلي/ }));

        const cloudInfoBtn = await within(dialog).findByRole('button', { name: /حول.*kimi-k2/ });
        fireEvent.click(cloudInfoBtn);
        const cloudBubble = await screen.findByRole('tooltip');
        expect(cloudBubble.querySelector('bdi[dir="ltr"]')).toHaveTextContent('kimi-k2');
        expect(cloudBubble).toHaveTextContent('#1');
        expect(cloudBubble).toHaveTextContent('الأفضل إجمالًا والأسرع');
        expect(cloudBubble).toHaveTextContent('للإدخال');
        expect(cloudBubble).toHaveTextContent('للإخراج');
        expect(cloudBubble.querySelector('.info-bubble-id')).toHaveAttribute('dir', 'ltr');

        fireEvent.click(cloudInfoBtn);
        const localInfoBtn = within(dialog).getByRole('button', { name: /حول.*Gemma-4-E4B-it-GGUF/ });
        fireEvent.click(localInfoBtn);
        const localBubble = await screen.findByRole('tooltip');
        expect(localBubble).toHaveTextContent('(متوقع)');
        expect(localBubble).toHaveTextContent('6 GB');
        expect(localBubble).toHaveTextContent('42 tok/s');
        expect(localBubble).toHaveTextContent('مقيسة هنا');
        expect(localBubble).toHaveTextContent('أضفته بنفسك');
        const ltrBdis = Array.from(localBubble.querySelectorAll('bdi[dir="ltr"]')).map((el) => el.textContent);
        expect(ltrBdis).toEqual(expect.arrayContaining(['6 GB', '42 tok/s']));
    });
});
