// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Under a turn that failed on gaiacpp: which agent ran, and the way back, taken only when asked. */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NativeAgentFailedNotice } from '../NativeAgentFailedNotice';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import type { SystemStatus } from '../../types';
import { AGENT_PROGRAMS, SETTINGS } from '../../test/setupApiMocks';

vi.mock('../../services/api');

const text = (el: HTMLElement) => stripIsolates(el.textContent ?? '');
const usePython = () => screen.getByRole('button', { name: 'Use the Python agent' });

beforeEach(() => {
    vi.resetAllMocks();
    useChatStore.setState({ locale: 'en', settingsSection: null, systemStatus: null });
    vi.mocked(api.getAgentPrograms).mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
    vi.mocked(api.updateSettings).mockResolvedValue({ ...SETTINGS, flagship_agent: 'gaiapy' });
});

describe('NativeAgentFailedNotice', () => {
    it('stays out of it when the model server is down: the Python agent would fail the same way', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, lemonade_probe: 'down' } as SystemStatus });
        render(<NativeAgentFailedNotice />);
        expect(screen.queryByRole('note')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use the Python agent' })).not.toBeInTheDocument();
    });

    it('still speaks up when the model server is down but the chat runs on a cloud model', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, default_model_is_cloud: true } as SystemStatus });
        render(<NativeAgentFailedNotice />);
        expect(usePython()).toBeInTheDocument();
    });

    it('speaks up while the model server is running', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: true } as SystemStatus });
        render(<NativeAgentFailedNotice />);
        expect(usePython()).toBeInTheDocument();
    });

    it('says the chat runs on gaiacpp (Beta) and changes nothing by itself', () => {
        render(<NativeAgentFailedNotice />);
        expect(text(screen.getByRole('note'))).toContain('This chat is running on gaiacpp (Beta), the native agent.');
        expect(api.getAgentPrograms).not.toHaveBeenCalled();
        expect(api.updateSettings).not.toHaveBeenCalled();
    });

    it('switches to the Python agent through the settings endpoint when asked', async () => {
        render(<NativeAgentFailedNotice />);
        await userEvent.click(usePython());
        expect(await screen.findByRole('status')).toHaveTextContent('Switched to the Python agent. It answers from your next message, so send this one again.');
        expect(api.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' });
    });

    it("shows the server's reason when the switch is refused mid-answer, and stays on gaiacpp", async () => {
        vi.mocked(api.updateSettings).mockRejectedValue(new Error('GAIA is answering in a chat, and switching agents now would cut that answer off.'));
        render(<NativeAgentFailedNotice />);
        await userEvent.click(usePython());
        expect(text(await screen.findByRole('alert'))).toContain("Couldn't switch to the Python agent: GAIA is answering in a chat");
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(usePython()).toBeEnabled();
    });

    it('refuses to switch to a Python agent that is not on this PC', async () => {
        vi.mocked(api.getAgentPrograms).mockResolvedValue({
            ...AGENT_PROGRAMS, current: 'gaiacpp',
            programs: [{ ...AGENT_PROGRAMS.programs[0], installed: false }, AGENT_PROGRAMS.programs[1]],
        });
        render(<NativeAgentFailedNotice />);
        await userEvent.click(usePython());
        expect(text(await screen.findByRole('alert'))).toContain('it isn’t installed on this PC, so gaiacpp stays your agent.');
        expect(api.updateSettings).not.toHaveBeenCalled();
    });

    it('opens Settings on Agent & model', async () => {
        render(<NativeAgentFailedNotice />);
        await userEvent.click(screen.getByRole('button', { name: /Open Settings › Agent & model/ }));
        await waitFor(() => expect(useChatStore.getState().settingsSection).toBe('model'));
    });
});
