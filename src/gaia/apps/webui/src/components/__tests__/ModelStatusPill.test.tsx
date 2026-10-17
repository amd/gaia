// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The sidebar's "Local · model" pill and its live status popover. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelStatusPill } from '../ModelStatusPill';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { LocalModelStatus, Session, SystemStatus } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 's1', title: 'Chat', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 0, document_ids: [], inference_remote: false,
};

const LOADED: LocalModelStatus = {
    state: 'loaded', active_model: 'Gemma-4-E4B-it-GGUF', active_display_name: 'Gemma 4 E4B', active_remote: false,
    model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B',
    placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
    ctx_size: 65536, memory: { source: 'AMD iGPU', total_gb: 112, free_gb: 80.5, model_gb: 8.2 },
    memory_error: null, error: null, base_url: 'http://localhost:13305/api/v1', owner: 'gaia', busy: false, restarting: false, restart_error: null,
};

const STOPPED: LocalModelStatus = {
    ...LOADED, state: 'stopped', model: null, display_name: null, placement: null, ctx_size: null, memory: null,
    error: "The model server isn't answering at http://localhost:13305/api/v1. Restart starts it if GAIA runs it.",
    error_detail: 'The local model server is not reachable at http://localhost:13305/api/v1 (connection refused).',
};

beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({
        active: {
            provider: 'local', model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', label: 'Gemma', remote: false,
            is_default: true, inference_remote: false,
        },
        names: {},
    });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, settingsSection: null,
        systemStatus: { lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as SystemStatus,
    });
    mockedApi.restartLocalModel.mockResolvedValue({ status: 'restarting', model: 'Gemma-4-E4B-it-GGUF', starting_server: false });
});

async function open() {
    fireEvent.click(screen.getByRole('button', { name: /Local · Gemma 4 E4B/ }));
    return screen.findByRole('dialog', { name: 'Model status' });
}

describe('ModelStatusPill', () => {
    it('shows the live state at a glance', () => {
        render(<ModelStatusPill />);
        expect(screen.getByRole('button', { name: 'Local · Gemma 4 E4B, model loaded' })).toBeInTheDocument();
        act(() => useChatStore.setState({ systemStatus: { lemonade_running: false, model_loaded: null } as SystemStatus }));
        expect(screen.getByRole('button', { name: 'Local · Gemma 4 E4B, model stopped' })).toBeInTheDocument();
    });

    it('opens the live status instead of Settings', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue(LOADED);
        render(<ModelStatusPill />);
        const dialog = await open();
        expect(useChatStore.getState().settingsSection).toBeNull();
        expect(await within(dialog).findByText('Loaded')).toBeInTheDocument();
        expect(within(dialog).getByText('GPU · Vulkan')).toBeInTheDocument();
        expect(within(dialog).getByText('~8.2 GB used by the model · 80.5 GB free')).toBeInTheDocument();
        expect(within(dialog).getByText('64K tokens')).toBeInTheDocument();
    });

    it('shows a stopped server and restarts it', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue(STOPPED);
        render(<ModelStatusPill />);
        const dialog = await open();
        expect(await within(dialog).findByText('Model server stopped')).toBeInTheDocument();
        expect(within(dialog).getByText(/isn't answering/)).toHaveAttribute('title', expect.stringMatching(/connection refused/));
        const restart = within(dialog).getByRole('button', { name: /Restart/ });
        expect(restart).toHaveAttribute('title', 'Start the model server and load the model');
        fireEvent.click(restart);
        await waitFor(() => expect(mockedApi.restartLocalModel).toHaveBeenCalled());
    });

    it('does not offer Restart while GAIA is answering', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...LOADED, busy: true });
        render(<ModelStatusPill />);
        const dialog = await open();
        expect(await within(dialog).findByText('Loaded · answering')).toBeInTheDocument();
        expect(within(dialog).getByRole('button', { name: /Restart/ })).toBeDisabled();
    });

    it('asks before reloading a model on the user\'s own Lemonade', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...LOADED, owner: 'user' });
        render(<ModelStatusPill />);
        const dialog = await open();
        fireEvent.click(await within(dialog).findByRole('button', { name: /Restart/ }));
        expect(mockedApi.restartLocalModel).not.toHaveBeenCalled();
        expect(within(dialog).getByRole('alert')).toHaveTextContent(
            'This reloads Gemma 4 E4B on your Lemonade at http://localhost:13305/api/v1. Other apps using it are interrupted',
        );
        fireEvent.click(within(dialog).getByRole('button', { name: 'Reload it' }));
        await waitFor(() => expect(mockedApi.restartLocalModel).toHaveBeenCalledWith(true));
    });

    it('never offers to start the user\'s own Lemonade', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...STOPPED, owner: 'user' });
        render(<ModelStatusPill />);
        const dialog = await open();
        await within(dialog).findByText('Model server stopped');
        expect(within(dialog).queryByRole('button', { name: /Restart/ })).not.toBeInTheDocument();
    });

    it('restarts GAIA\'s own server without asking', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue(LOADED);
        render(<ModelStatusPill />);
        const dialog = await open();
        fireEvent.click(await within(dialog).findByRole('button', { name: /Restart/ }));
        await waitFor(() => expect(mockedApi.restartLocalModel).toHaveBeenCalledWith(false));
    });

    it('says Local before the first chat, once setup is done', () => {
        useChatStore.setState({ sessions: [], currentSessionId: null });
        render(<ModelStatusPill />);
        expect(screen.getByRole('button', { name: 'Local · Gemma 4 E4B, model loaded' })).toBeInTheDocument();
    });

    it('shows why a restart failed', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...LOADED, restart_error: 'Restarting Gemma 4 E4B failed: out of memory' });
        render(<ModelStatusPill />);
        const dialog = await open();
        expect(await within(dialog).findByText(/out of memory/)).toBeInTheDocument();
    });

    it('warns when the model fell back to the CPU', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({
            ...LOADED,
            placement: { device: 'cpu', backend: 'cpu', label: 'CPU', source: 'reported', warning: 'Running on the CPU instead of the GPU, so replies will be slow.' },
        });
        render(<ModelStatusPill />);
        const dialog = await open();
        expect(await within(dialog).findByText(/Running on the CPU instead of the GPU/)).toBeInTheDocument();
    });

    it('links to Model settings', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue(LOADED);
        render(<ModelStatusPill />);
        const dialog = await open();
        fireEvent.click(within(dialog).getByRole('button', { name: /Model settings/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });
});
