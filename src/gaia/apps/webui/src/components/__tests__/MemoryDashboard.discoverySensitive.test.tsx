// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Profile setup's discovery review hides a sensitive finding's text until the
 * user asks to see it, and the progress log never prints it (#8540).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

const SECRET = 'Bank PIN hint 4321';

beforeEach(() => {
    vi.resetAllMocks();
    useChatStore.setState({ sessions: [], showMemoryDashboard: true });
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: {
            total: 0, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0,
            entity_count: 0, avg_confidence: 0, oldest: null, newest: null,
        },
        conversations: { total_turns: 0, total_sessions: 0, first_session: null, last_session: null },
        tools: { total_calls: 0, unique_tools: 0, overall_success_rate: 0, total_errors: 0 },
        temporal: { upcoming_count: 0, overdue_count: 0 },
        db_size_bytes: 0,
    } as never);
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 15 } as never);
    mockedApi.getToolSummary.mockResolvedValue([] as never);
    mockedApi.getMemoryConversations.mockResolvedValue([] as never);
    mockedApi.getUpcomingItems.mockResolvedValue([] as never);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0 });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 } as never);
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    } as never);
});

describe('Profile setup discovery review', () => {
    it('hides a sensitive finding until asked and keeps it out of the log', async () => {
        const es = { onmessage: null as ((e: { data: string }) => void) | null, onerror: null as (() => void) | null, close: vi.fn() };
        mockedApi.openDiscoveryStream.mockReturnValue(es as never);
        render(<MemoryDashboard />);
        fireEvent.click(screen.getByRole('tab', { name: /Profile Setup/i }));
        fireEvent.click(screen.getByRole('button', { name: /Start Scan/i }));
        act(() => {
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: 'Uses VS Code', category: 'fact', context: 'global', confidence: 0.9 } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: SECRET, category: 'fact', context: 'global', confidence: 0.9, sensitive: true } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'done', total: 2 }) });
        });

        await screen.findByText('Review & Save');
        expect(screen.getByText('Uses VS Code')).toBeInTheDocument();
        expect(screen.getByText('Found a sensitive item')).toBeInTheDocument();
        expect(document.body.textContent).not.toContain(SECRET);
        expect(document.body.innerHTML).not.toContain(SECRET);

        const checkboxes = screen.getAllByRole('checkbox').filter(c => c.closest('.mem-review-item'));
        fireEvent.click(screen.getByRole('button', { name: /Sensitive — show/ }));
        expect(screen.getByText(SECRET)).toBeInTheDocument();
        // Revealing doesn't change what will be saved.
        expect(checkboxes.every(c => (c as HTMLInputElement).checked)).toBe(true);
        expect(document.querySelector('.mem-log')!.textContent).not.toContain(SECRET);
    });
});
