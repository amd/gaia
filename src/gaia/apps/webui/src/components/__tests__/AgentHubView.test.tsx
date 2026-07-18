// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentHubView } from '../AgentHubView';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listCatalog.mockResolvedValue({ agents: [], total: 0, offline: false });
    useChatStore.setState({ agents: [], activeAgentId: 'chat', agentsError: null });
});

describe('AgentHubView (#2118)', () => {
    it('renders the hub even with no registered agents (consumer install)', () => {
        render(<AgentHubView onStartChat={vi.fn()} onCreateAgent={vi.fn()} onRetryAgents={vi.fn()} />);
        // Reachable header + the Available tab (install path) is present.
        expect(screen.getByRole('heading', { name: 'Agent Hub' })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: /Available/i })).toBeInTheDocument();
    });

    it('surfaces a loud, actionable error when agent discovery fails', () => {
        useChatStore.setState({ agentsError: 'Server not reachable' });
        const onRetry = vi.fn();
        render(<AgentHubView onStartChat={vi.fn()} onCreateAgent={vi.fn()} onRetryAgents={onRetry} />);
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent('Server not reachable');
        screen.getByRole('button', { name: /Retry/i }).click();
        expect(onRetry).toHaveBeenCalledTimes(1);
    });
});
