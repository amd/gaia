// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The Memory page reads only the Python agent's store, so with gaiacpp chosen
 * it must say that gaiacpp's own memory isn't shown here.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as api from '../../services/api';
import * as memoryApi from '../../services/memoryApi';
import type { Settings } from '../../types';

vi.mock('../../services/api');
vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(api);
const mockedMemoryApi = vi.mocked(memoryApi);

function chooseAgent(flagship_agent: string) {
    mockedApi.getSettings.mockResolvedValue({ flagship_agent } as Settings);
}

beforeEach(() => {
    vi.resetAllMocks();
    mockedMemoryApi.getMemoryStats.mockResolvedValue({ knowledge: { total: 0 }, conversations: {}, tools: {} });
    mockedMemoryApi.getMemoryActivity.mockResolvedValue([]);
    mockedMemoryApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 25 });
    mockedMemoryApi.getToolSummary.mockResolvedValue([]);
    mockedMemoryApi.getMemoryConversations.mockResolvedValue([]);
    mockedMemoryApi.getUpcomingItems.mockResolvedValue([]);
    mockedMemoryApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0 });
    mockedMemoryApi.getEntities.mockResolvedValue([]);
    mockedMemoryApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedMemoryApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedMemoryApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
});

describe('MemoryDashboard agent program notice', () => {
    it('says gaiacpp keeps its own memory when gaiacpp is the agent', async () => {
        chooseAgent('gaiacpp');
        render(<MemoryDashboard />);
        expect(await screen.findByRole('note')).toHaveTextContent(/gaiacpp keeps its own memory/);
    });

    it('shows no notice for the Python agent', async () => {
        chooseAgent('gaiapy');
        render(<MemoryDashboard />);
        await waitFor(() => expect(mockedApi.getSettings).toHaveBeenCalled());
        await waitFor(() => expect(mockedMemoryApi.getMemorySettings).toHaveBeenCalled());
        expect(screen.queryByText(/gaiacpp keeps its own memory/)).toBeNull();
    });
});
