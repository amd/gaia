// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

const TITLED_ID = 'c2639b15-1111-2222-3333-444455556666';
const DELETED_ID = 'deadbeef-1111-2222-3333-444455556666';

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: {
            total: 0, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0,
            entity_count: 0, avg_confidence: 0, oldest: null, newest: null,
        },
        conversations: { total_turns: 2, total_sessions: 2, first_session: null, last_session: null },
        tools: { total_calls: 0, unique_tools: 0, overall_success_rate: 0, total_errors: 0 },
        temporal: { upcoming_count: 0, overdue_count: 0 },
        db_size_bytes: 0,
    } as never);
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([
        { session_id: TITLED_ID, turn_count: 2, first_message: 'Please remember teal', last_activity: '2026-10-16T00:00:00Z', consolidated: false },
        { session_id: DELETED_ID, turn_count: 1, first_message: 'Old chat', last_activity: '2026-10-15T00:00:00Z', consolidated: false },
    ] as never);
    mockedApi.getConversationDetail.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({
        total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0,
    });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
    useChatStore.setState({
        sessions: [{ id: TITLED_ID, title: 'Remember teal and Calgary office' } as Session],
        currentSessionId: null,
        showMemoryDashboard: true,
    });
});

it('labels a conversation by its chat title, not its session ID', async () => {
    render(<MemoryDashboard />);
    // Stats and the knowledge table live under Advanced.
    fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

    expect(await screen.findByText('Remember teal and Calgary office')).toBeInTheDocument();
    expect(screen.queryByText(/c2639b15/)).not.toBeInTheDocument();
});

it('keeps the short ID for a chat the sidebar no longer has', async () => {
    render(<MemoryDashboard />);
    // Stats and the knowledge table live under Advanced.
    fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

    expect(await screen.findByText('deadbeef...')).toBeInTheDocument();
});

it('opens the chat from the conversation detail', async () => {
    render(<MemoryDashboard />);
    // Stats and the knowledge table live under Advanced.
    fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
    fireEvent.click(await screen.findByText('Remember teal and Calgary office'));
    fireEvent.click(await screen.findByRole('button', { name: 'Open chat' }));

    expect(useChatStore.getState().currentSessionId).toBe(TITLED_ID);
    expect(useChatStore.getState().showMemoryDashboard).toBe(false);
});
