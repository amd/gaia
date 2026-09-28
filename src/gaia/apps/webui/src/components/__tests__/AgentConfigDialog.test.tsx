// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Issue #4300: the dialog must only close once the settings are persisted,
 * and must show the error when persisting fails.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AgentConfigDialog } from '../AgentConfigDialog';
import { useAgentStore } from '../../stores/agentStore';
import type { AgentInfo } from '../../types/agent';

const AGENT: AgentInfo = {
    id: 'email',
    name: 'Email',
    description: 'Email triage agent',
    version: '1.0.0',
    binaries: {},
    toolsCount: 0,
    categories: [],
    requiresAdmin: false,
    capabilities: { standaloneMode: true, notifications: false, interactiveChat: true },
};

function installBridge(setConfig: (update: unknown) => Promise<unknown>) {
    (window as unknown as { gaiaAPI: unknown }).gaiaAPI = {
        tray: { getConfig: vi.fn(), setConfig: vi.fn(setConfig) },
    };
}

describe('AgentConfigDialog save (#4300)', () => {
    beforeEach(() => {
        useAgentStore.setState({ agents: { email: AGENT }, configs: {}, statuses: {}, lastError: null });
    });
    afterEach(() => {
        delete (window as unknown as { gaiaAPI?: unknown }).gaiaAPI;
    });

    it('closes once the save is persisted', async () => {
        const saved = { autoStart: true, restartOnCrash: true, logLevel: 'info' };
        installBridge(async () => ({ agents: { email: saved } }));
        const onClose = vi.fn();
        render(<AgentConfigDialog agentId="email" onClose={onClose} />);

        fireEvent.click(screen.getByLabelText('Toggle auto-start'));
        fireEvent.click(screen.getByText('Save Changes'));

        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(useAgentStore.getState().configs.email).toEqual(saved);
    });

    it('stays open and shows the error when the save fails', async () => {
        installBridge(async () => {
            throw new Error('permission denied');
        });
        const onClose = vi.fn();
        render(<AgentConfigDialog agentId="email" onClose={onClose} />);

        fireEvent.click(screen.getByLabelText('Toggle auto-start'));
        fireEvent.click(screen.getByText('Save Changes'));

        expect(await screen.findByRole('alert')).toHaveTextContent(/permission denied/);
        expect(onClose).not.toHaveBeenCalled();
    });
});
