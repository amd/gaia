// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AdvancedSettings } from '../AdvancedSettings';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import type { SystemStatus } from '../../../types';

vi.mock('../../../services/api');
const modelActions = vi.hoisted(() => ({
    downloadError: null as string | null,
    downloadModel: vi.fn(),
}));
vi.mock('../../../hooks/useModelActions', () => ({
    useModelActions: () => ({
        isLoadingModel: false, isDownloadingModel: false, downloadError: modelActions.downloadError,
        loadModel: vi.fn(), downloadModel: modelActions.downloadModel,
    }),
}));

const mockedApi = vi.mocked(api);

function status(overrides: Partial<SystemStatus>): SystemStatus {
    return {
        lemonade_running: true, model_loaded: 'amd.o1-mini', embedding_model_loaded: true,
        disk_space_gb: 800, memory_available_gb: 30, initialized: true, version: '0.25.0',
        lemonade_version: '2026.40.0', model_size_gb: null, model_device: 'none',
        model_context_size: null, model_labels: ['cloud', 'chat'], gpu_name: null,
        gpu_vram_gb: null, tokens_per_second: null, time_to_first_token: null,
        processor_name: null, context_size_sufficient: true, model_downloaded: null,
        default_model_name: 'amd.DeepSeek-V4.1-Flash', default_model_size_gb: null,
        lemonade_url: 'http://localhost:13305', expected_model_loaded: false,
        download_progress: null, active_profile: 'gaia',
        ...overrides,
    } as SystemStatus;
}

beforeEach(() => {
    vi.clearAllMocks();
    modelActions.downloadError = null;
    modelActions.downloadModel.mockReset();
    useChatStore.getState().setSystemStatus(null);
    mockedApi.getMCPRuntimeStatus.mockResolvedValue({ servers: [] } as never);
    mockedApi.getLemonadeTarget.mockResolvedValue({
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
        api_key_stored: false, gaia_installed: true, offer: false, what_gaia_does: '',
    });
    mockedApi.getModelServer.mockResolvedValue({
        reachable: false, error: 'Lemonade is not answering at http://localhost:13305/api/v1.',
        base_url: 'http://localhost:13305/api/v1', owner: 'user',
    });
    mockedApi.getSettings.mockResolvedValue(
        { custom_model: null, dynamic_tools: false, dynamic_tools_locked: false } as never,
    );
});

describe('AdvancedSettings', () => {
    it('offers no local model actions for a cloud model', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({ default_model_is_cloud: true }));
        render(<AdvancedSettings />);
        await screen.findByText('amd.o1-mini');

        expect(screen.queryByText('Wrong model loaded.')).not.toBeInTheDocument();
        expect(screen.queryByText('Download again')).not.toBeInTheDocument();
        expect(screen.queryByText('Device')).not.toBeInTheDocument();
        expect(screen.queryByText('Context Size')).not.toBeInTheDocument();
        expect(screen.queryByText(/Expected:/)).not.toBeInTheDocument();
    });

    it('rounds the GPU memory Lemonade reports', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({ gpu_name: 'Apple M4', gpu_vram_gb: 11.840011596679688 }));
        render(<AdvancedSettings />);

        expect(await screen.findByText('Apple M4 (11.8 GB)')).toBeInTheDocument();
    });

    it.each([
        [128, 'Radeon 8060S (128.0 GB)'],
        [0.5, 'Radeon 8060S (under 1 GB)'],
        [0, 'Radeon 8060S'],
        [null, 'Radeon 8060S'],
    ])('shows %s GB of GPU memory as "%s"', async (vram, shown) => {
        mockedApi.getSystemStatus.mockResolvedValue(status({ gpu_name: 'Radeon 8060S', gpu_vram_gb: vram }));
        render(<AdvancedSettings />);

        expect(await screen.findByText(shown)).toBeInTheDocument();
    });

    it('keeps them for a local model', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: 'Qwen3-4B-GGUF', model_device: 'gpu',
            default_model_name: 'Gemma-4-E4B-it-GGUF', model_labels: null,
        }));
        render(<AdvancedSettings />);
        await screen.findByText('Wrong model loaded.');

        expect(screen.getByText('Download again')).toBeInTheDocument();
        expect(screen.getByText('Device')).toBeInTheDocument();
        // Context size lives in Settings › Model, sized to what fits (#5213).
        expect(screen.queryByText('Context Size')).not.toBeInTheDocument();
    });

    it('shows why Force Re-download could not start', async () => {
        modelActions.downloadError = "Cannot download 'user.x': add it from its Hugging Face link.";
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: 'Qwen3-4B-GGUF', model_device: 'gpu',
            default_model_name: 'Gemma-4-E4B-it-GGUF', model_labels: null,
        }));
        render(<AdvancedSettings />);
        await screen.findByText('Download again');

        expect(screen.getByText(/Cannot download 'user.x'/)).toHaveAttribute('role', 'alert');
    });

    it('Force Re-download keeps the model in use, and loads the repaired one when none is', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: 'Qwen3-4B-GGUF', model_device: 'gpu',
            default_model_name: 'Gemma-4-E4B-it-GGUF', model_labels: null,
        }));
        const { unmount } = render(<AdvancedSettings />);
        fireEvent.click(await screen.findByText('Download again'));
        expect(modelActions.downloadModel).toHaveBeenCalledWith(true, undefined, false);
        unmount();

        modelActions.downloadModel.mockReset();
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: null,
            default_model_name: 'Gemma-4-E4B-it-GGUF', model_labels: null,
        }));
        render(<AdvancedSettings />);
        fireEvent.click(await screen.findByText('Download again'));
        expect(modelActions.downloadModel).toHaveBeenCalledWith(true, undefined, true);
    });

    it('shows why Download could not start, once', async () => {
        modelActions.downloadError = "Cannot download 'user.x': add it from its Hugging Face link.";
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: null, model_downloaded: false,
            default_model_name: 'user.x', model_labels: null,
        }));
        render(<AdvancedSettings />);
        const download = await screen.findByText('Download');
        fireEvent.click(download);

        expect(modelActions.downloadModel).toHaveBeenCalledWith(false, undefined, true);
        expect(screen.getAllByText(/Cannot download 'user.x'/)).toHaveLength(1);
    });

    it('shows no download error when there is none', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: null, model_downloaded: false,
            default_model_name: 'user.x', model_labels: null,
        }));
        render(<AdvancedSettings />);
        await screen.findByText('Download');

        expect(screen.queryByText(/Cannot download/)).not.toBeInTheDocument();
    });

    it('offers setup, not waiting, when no model server is installed', async () => {
        const notInstalled = status({
            lemonade_running: false, model_loaded: null, start_command: 'gaia init',
            start_instruction: 'Lemonade Server is not installed. Run `gaia init` to install it.',
        });
        mockedApi.getSystemStatus.mockResolvedValue(notInstalled);
        // The app's status poll fills the store the model server notice reads.
        useChatStore.getState().setSystemStatus(notInstalled);
        const opened = vi.fn();
        window.addEventListener('gaia:set-up-local', opened);
        render(<AdvancedSettings />);

        expect(await screen.findByText('Not Installed')).toBeInTheDocument();
        expect(screen.getByText('Set it up to run models on this PC. Cloud providers still work.')).toBeInTheDocument();
        expect(screen.queryByText(/normally starts on its own/)).not.toBeInTheDocument();
        // The first sits on the status card; the closed server section carries another.
        fireEvent.click(screen.getAllByRole('button', { name: 'Set up local models' })[0]);
        expect(opened).toHaveBeenCalledOnce();
        window.removeEventListener('gaia:set-up-local', opened);
    });

    it('keeps a stopped server that is installed on its own start hint, with no setup button', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({
            lemonade_running: false, model_loaded: null, start_command: null,
            start_instruction: 'Start Lemonade from the tray icon.',
        }));
        render(<AdvancedSettings />);

        expect(await screen.findByText('Start Lemonade from the tray icon.')).toBeInTheDocument();
        expect(screen.getByText('Not Running')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Set up local models' })).not.toBeInTheDocument();
    });

    describe('Custom model ID', () => {
        async function save(id: string, modelStatus: { found: boolean; checked: boolean }) {
            mockedApi.getSystemStatus.mockResolvedValue(status({}));
            mockedApi.updateSettings.mockResolvedValue({
                custom_model: id, model_status: { downloaded: false, loaded: false, ...modelStatus },
                dynamic_tools: false, dynamic_tools_locked: false,
            } as never);
            render(<AdvancedSettings />);
            const input = await screen.findByRole('textbox', { name: 'Custom model override' });
            await waitFor(() => expect(input).toBeEnabled());
            fireEvent.change(input, { target: { value: id } });
            fireEvent.click(screen.getByRole('button', { name: 'Save custom model' }));
            await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledWith({ custom_model: id }));
        }

        it('says when the model server does not list the saved ID', async () => {
            await save('fireworks.no-such-model', { found: false, checked: true });
            expect(await screen.findByText(
                "Saved, but the model server doesn't list “fireworks.no-such-model”. Check the ID: messages will fail if the model doesn't exist.",
            )).toBeInTheDocument();
        });

        it('says when the ID could not be checked', async () => {
            await save('Qwen3-4B-GGUF', { found: false, checked: false });
            expect(await screen.findByText(
                "Saved, but GAIA couldn't check this ID because the model server isn't answering.",
            )).toBeInTheDocument();
        });

        it('adds nothing for an ID the model server lists', async () => {
            await save('Qwen3-4B-GGUF', { found: true, checked: true });
            expect(await screen.findByText('Saved')).toBeInTheDocument();
            expect(screen.queryByText(/Saved, but/)).not.toBeInTheDocument();
        });

        it('still carries the note when Settings is opened again on the saved bad ID', async () => {
            mockedApi.getSystemStatus.mockResolvedValue(status({}));
            mockedApi.getSettings.mockResolvedValue({
                custom_model: 'fireworks.no-such-model',
                model_status: { found: false, checked: true, downloaded: false, loaded: false },
                dynamic_tools: false, dynamic_tools_locked: false,
            } as never);
            render(<AdvancedSettings />);

            expect(await screen.findByDisplayValue('fireworks.no-such-model')).toBeInTheDocument();
            expect(await screen.findByText(/the model server doesn't list “fireworks.no-such-model”/)).toBeInTheDocument();
            expect(mockedApi.updateSettings).not.toHaveBeenCalled();
        });

        it('opens with no note on a saved ID the model server lists', async () => {
            mockedApi.getSystemStatus.mockResolvedValue(status({}));
            mockedApi.getSettings.mockResolvedValue({
                custom_model: 'Qwen3-4B-GGUF',
                model_status: { found: true, checked: true, downloaded: true, loaded: false },
                dynamic_tools: false, dynamic_tools_locked: false,
            } as never);
            render(<AdvancedSettings />);

            await screen.findByDisplayValue('Qwen3-4B-GGUF');
            expect(screen.queryByText(/Saved, but/)).not.toBeInTheDocument();
        });

        it('drops the note once the ID is edited', async () => {
            await save('fireworks.no-such-model', { found: false, checked: true });
            await screen.findByText(/Saved, but the model server/);
            fireEvent.change(screen.getByRole('textbox', { name: 'Custom model override' }), { target: { value: 'x' } });
            expect(screen.queryByText(/Saved, but/)).not.toBeInTheDocument();
        });
    });
});

describe('AdvancedSettings capabilities', () => {
    afterEach(() => useChatStore.setState({ locale: 'en' }));

    /** The text a reader sees, without the bidi isolate marks around each half. */
    const seen = (text: string | null) => (text ?? '').replace(/[⁦-⁩]/g, '');

    it('names capabilities in words, not Lemonade ids', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({ model_labels: ['cloud', 'chat', 'vision', 'tool-calling'] }));
        render(<AdvancedSettings />);
        expect(await screen.findByText('cloud, chat, vision, tool calling')).toBeInTheDocument();
    });

    it('names capabilities in Arabic, keeping a label it has no word for', async () => {
        useChatStore.setState({ locale: 'ar' });
        mockedApi.getSystemStatus.mockResolvedValue(status({ model_labels: ['chat', 'tool-calling', 'mtp'] }));
        render(<AdvancedSettings />);
        const row = await screen.findByText((_, el) => el?.classList.contains('status-value') === true && seen(el.textContent).startsWith('محادثة'));
        expect(seen(row.textContent)).toBe('محادثة، استدعاء الأدوات، mtp (chat, tool calling, mtp)');
    });
});
