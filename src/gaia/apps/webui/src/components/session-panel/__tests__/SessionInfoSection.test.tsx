// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Session: the chat's id and the versions behind it, for a bug report. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionInfoSection } from '../SessionInfoSection';
import { useChatStore } from '../../../stores/chatStore';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import { resetPanelState, SID } from './testUtils';

const writeText = vi.fn<(text: string) => Promise<void>>();

beforeEach(() => {
    vi.clearAllMocks();
    resetPanelState();
    writeText.mockResolvedValue();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    useChatStore.setState({
        sessions: [{
            id: SID, title: 't', created_at: '2026-11-13T20:47:25Z', updated_at: '', model: 'm', system_prompt: null,
            message_count: 2, document_ids: [], agent_type: 'gaia',
        } as never],
        systemStatus: { version: '0.26.0', lemonade_version: '10.8.1' } as never,
    });
});

describe('Session', () => {
    it('is collapsed until asked for', () => {
        render(<SessionInfoSection sessionId={SID} />);
        expect(screen.getByRole('button', { name: 'Session' })).toHaveAttribute('aria-expanded', 'false');
        expect(screen.queryByText(SID)).not.toBeInTheDocument();
    });

    it('shows the chat id, its agent and the versions behind it', () => {
        useSessionPanelStore.getState().setSection('session', true);
        render(<SessionInfoSection sessionId={SID} />);
        expect(screen.getByText(SID)).toBeInTheDocument();
        expect(screen.getByText('gaia')).toBeInTheDocument();
        expect(screen.getByText('0.26.0')).toBeInTheDocument();
        expect(screen.getByText('10.8.1')).toBeInTheDocument();
        expect(screen.getByText('Started')).toBeInTheDocument();
    });

    it('copies the chat id', async () => {
        useSessionPanelStore.getState().setSection('session', true);
        render(<SessionInfoSection sessionId={SID} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy chat ID' }));
        await waitFor(() => expect(writeText).toHaveBeenCalledWith(SID));
    });

    it('says why when the id could not be copied', async () => {
        writeText.mockRejectedValue(new Error('clipboard blocked'));
        useSessionPanelStore.getState().setSection('session', true);
        render(<SessionInfoSection sessionId={SID} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy chat ID' }));
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't copy the chat ID: clipboard blocked");
    });
});
