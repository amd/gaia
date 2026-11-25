// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentChip } from '../AgentChip';
import { useActiveAgentStore } from '../../stores/activeAgentStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { AGENT_PROGRAMS } from '../../test/setupApiMocks';
import { log } from '../../utils/logger';

vi.mock('../../services/api');
const getAgentPrograms = vi.mocked(api.getAgentPrograms);

beforeEach(() => {
    vi.clearAllMocks();
    useActiveAgentStore.setState({ current: null });
    useChatStore.setState({ settingsSection: null });
});

describe('AgentChip', () => {
    it('says Fast and light is the agent, and opens Settings at Agent & model', async () => {
        getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
        render(<AgentChip />);
        const chip = await screen.findByRole('button', { name: 'Fast and light · Beta' });
        expect(chip).toHaveClass('composer-chip');
        expect(chip).not.toHaveClass('is-on');

        await userEvent.click(chip);
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('shows nothing for the default Full-featured agent', async () => {
        getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiapy' });
        const { container } = render(<AgentChip />);
        await waitFor(() => expect(useActiveAgentStore.getState().current).toBe('gaiapy'));
        expect(container).toBeEmptyDOMElement();
    });

    it('shows nothing while the agent is not known yet', () => {
        getAgentPrograms.mockReturnValue(new Promise(() => {}));
        const { container } = render(<AgentChip />);
        expect(container).toBeEmptyDOMElement();
    });

    it('shows nothing, and logs why, when the agent cannot be read', async () => {
        const warn = vi.spyOn(log.api, 'warn').mockImplementation(() => {});
        useActiveAgentStore.setState({ current: 'gaiacpp' });
        getAgentPrograms.mockRejectedValue(new Error('the server is not answering'));
        const { container } = render(<AgentChip />);
        await waitFor(() => expect(warn).toHaveBeenCalledWith('Could not read which agent is in use', expect.any(Error)));
        expect(container).toBeEmptyDOMElement();
    });

    it('reads the agent again when Settings closes', async () => {
        getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiapy' });
        useChatStore.setState({ settingsSection: 'model' });
        render(<AgentChip />);
        expect(getAgentPrograms).not.toHaveBeenCalled();

        getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByRole('button', { name: 'Fast and light · Beta' })).toBeInTheDocument();
    });

    it('goes away as soon as this app switches back to Full-featured', async () => {
        getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
        render(<AgentChip />);
        await screen.findByRole('button', { name: 'Fast and light · Beta' });
        act(() => useActiveAgentStore.getState().setCurrent('gaiapy'));
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });
});
