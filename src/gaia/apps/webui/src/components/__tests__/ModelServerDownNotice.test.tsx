// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The fix offered under a turn that failed because the model server was down (#6695). */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelServerDownNotice } from '../ModelServerDownNotice';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import type { LemonadeTarget, LocalModelStatus } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const STOPPED: LocalModelStatus = {
    state: 'stopped', active_model: 'fireworks.glm-5', active_display_name: 'glm-5', active_remote: true,
    model: null, display_name: null, placement: null, ctx_size: null, memory: null, memory_error: null,
    error: "The model server isn't answering at http://localhost:13305/api/v1. Restart starts it.",
    base_url: 'http://localhost:13305/api/v1', owner: 'gaia', busy: false, restarting: false, restart_error: null,
};

const OWN: LemonadeTarget = {
    mode: 'gaia', saved_mode: 'gaia', source: 'gaia', base_url: 'http://localhost:13305/api/v1',
    saved_base_url: null, api_key_stored: false, gaia_installed: true, offer: false, what_gaia_does: '',
};

const text = (el: HTMLElement) => stripIsolates(el.textContent ?? '');

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ locale: 'en' });
    mockedApi.getLocalModelStatus.mockResolvedValue(STOPPED);
    mockedApi.getLemonadeTarget.mockResolvedValue(OWN);
    mockedApi.restartLocalModel.mockResolvedValue({ status: 'restarting', model: 'fireworks.glm-5', starting_server: true });
});

describe('ModelServerDownNotice', () => {
    it('names the local server and starts GAIA’s own', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            render(<ModelServerDownNotice />);
            const alert = await screen.findByRole('alert');
            expect(text(alert)).toContain('The local model server isn’t answering');
            expect(text(alert)).toContain('cloud models included');

            mockedApi.getLocalModelStatus.mockResolvedValue({ ...STOPPED, state: 'idle' });
            fireEvent.click(await screen.findByRole('button', { name: 'Start the model server' }));
            await vi.advanceTimersByTimeAsync(2_500);
            expect(mockedApi.restartLocalModel).toHaveBeenCalledWith(false);
            await waitFor(() => expect(text(screen.getByRole('status'))).toContain('running again'));
        } finally {
            vi.useRealTimers();
        }
    });

    it('offers GAIA’s own server when the user’s own is down', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...STOPPED, owner: 'user' });
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...OWN, mode: 'external', source: 'config' });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...OWN, daemon: null });
        render(<ModelServerDownNotice />);

        fireEvent.click(await screen.findByRole('button', { name: 'Use GAIA’s own server' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledWith({ mode: 'gaia' }));
        expect(await screen.findByRole('status')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Start the model server' })).toBeNull();
    });

    it('says how to go on when GAIA can neither start nor switch the server', async () => {
        mockedApi.getLocalModelStatus.mockResolvedValue({ ...STOPPED, owner: 'user' });
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...OWN, mode: 'external', source: 'env' });
        render(<ModelServerDownNotice />);

        await waitFor(() => expect(text(screen.getByRole('alert'))).toContain('GAIA doesn’t run this server'));
        expect(screen.queryByRole('button', { name: 'Start the model server' })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Use GAIA’s own server' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        await waitFor(() => expect(text(screen.getByRole('alert'))).toContain('still isn’t answering'));
    });

    it('shows a start that failed, with the reason', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            render(<ModelServerDownNotice />);
            mockedApi.getLocalModelStatus.mockResolvedValue({ ...STOPPED, restart_error: 'Restarting glm-5 failed: port in use' });
            fireEvent.click(await screen.findByRole('button', { name: 'Start the model server' }));
            await vi.advanceTimersByTimeAsync(2_500);
            await waitFor(() => expect(text(screen.getByRole('alert'))).toContain('port in use'));
            expect(screen.getByRole('button', { name: 'Start the model server' })).toBeEnabled();
        } finally {
            vi.useRealTimers();
        }
    });

    it('is translated', async () => {
        useChatStore.setState({ locale: 'ar' });
        render(<ModelServerDownNotice />);
        const alert = await screen.findByRole('alert');
        expect(text(alert)).toContain('خادم النماذج المحلي لا يستجيب');
        expect(await screen.findByRole('button', { name: /تشغيل خادم النماذج/ })).toBeInTheDocument();
    });
});
