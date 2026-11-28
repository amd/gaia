// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';
import { NATIVE_MEMORY_STORE, PYTHON_MEMORY_STORE } from '../../test/memoryStore';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

beforeEach(() => {
    vi.resetAllMocks();
    useChatStore.setState({ settingsSection: null });
    mockedApi.getMemoryStore.mockResolvedValue({ ...PYTHON_MEMORY_STORE, other_available: true });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: {
            total: 3, total_retrievals: 5, by_category: {}, by_context: {},
            sensitive_count: 1, entity_count: 2, avg_confidence: 0.85, oldest: null, newest: null,
        },
        conversations: { total_turns: 4, total_sessions: 1, first_session: null, last_session: null },
        tools: { total_calls: 2, unique_tools: 1, overall_success_rate: 1, total_errors: 0 },
        temporal: { upcoming_count: 0, overdue_count: 0 },
        db_size_bytes: 4096,
    });
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 15 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({
        total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0,
    });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: { pending_approval: 2 }, tasks: {} });
});

describe('MemoryDashboard agent switch', () => {
    it('clears Full-featured database stats while keeping Goals after switching to Fast and light', async () => {
        const { container } = render(<MemoryDashboard />);
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

        await waitFor(() => expect(container.querySelector('.mem-db-footer')).not.toBeNull());
        expect(container.querySelector('.mem-db-footer')?.textContent).toContain('0.85');
        expect(container.querySelector('.mem-tab-badge')?.textContent).toBe('2');

        const statsCallsBefore = mockedApi.getMemoryStats.mock.calls.length;

        // Open Settings, switch the active memory store to gaiacpp, and close Settings.
        act(() => { useChatStore.getState().openSettings('model'); });
        mockedApi.getMemoryStore.mockResolvedValue(NATIVE_MEMORY_STORE);
        act(() => { useChatStore.getState().closeSettings(); });

        await screen.findByText(/Showing what Fast and light remembers/);
        await waitFor(() => {
            expect(container.querySelector('.mem-db-footer')).toBeNull();
        });
        expect(container.querySelector('.mem-tab-badge')?.textContent).toBe('2');
        expect(mockedApi.getMemoryStats).toHaveBeenCalledTimes(statsCallsBefore);
    });
});
