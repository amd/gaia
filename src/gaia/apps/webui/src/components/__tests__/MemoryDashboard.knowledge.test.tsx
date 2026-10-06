// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Knowledge-browser row actions and the state they must keep current:
 * delete needs a confirming click, an edit refreshes the "% embedded"
 * indicator, a save the backend could not embed says so, and the tool stats
 * say memory tools are not counted. The layout contract pins the CSS that keeps
 * wide tables scrolling inside their own box instead of the whole panel.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import dashboardCss from '../MemoryDashboard.css?raw';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

const ENTRY = {
    id: 'k1', category: 'preference', content: 'Prefers dark mode', domain: null,
    source: 'user', confidence: 0.8, metadata: null, use_count: 0, context: 'work',
    sensitive: false, entity: null, created_at: '2026-10-01T10:00:00+00:00',
    updated_at: '2026-10-01T10:00:00+00:00', last_used: null, due_at: null,
    reminded_at: null, superseded_by: null,
};

const coverage = (pct: number) => ({
    total_items: 4, with_embedding: Math.round(pct / 25), without_embedding: 4 - Math.round(pct / 25), coverage_pct: pct,
});

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: { total: 1 }, conversations: {}, tools: { total_calls: 2, unique_tools: 1 },
    });
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [ENTRY], total: 1, offset: 0, limit: 25 });
    mockedApi.getToolSummary.mockResolvedValue([
        { tool_name: 'write_file', total_calls: 2, success_count: 2, failure_count: 0, success_rate: 1, avg_duration_ms: 10, last_used: null, last_error: null },
    ]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue(coverage(100));
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
    mockedApi.deleteKnowledge.mockResolvedValue({ status: 'deleted' });
});

async function renderTable() {
    render(<MemoryDashboard />);
    const cell = await screen.findByText('Prefers dark mode', { selector: '.mem-content-cell *, .mem-content-cell' });
    return cell.closest('tr')!;
}

describe('MemoryDashboard knowledge rows', () => {
    it('deletes a memory only after a confirming click', async () => {
        const user = userEvent.setup();
        const row = await renderTable();

        await user.click(within(row).getByRole('button', { name: 'Delete memory' }));
        expect(mockedApi.deleteKnowledge).not.toHaveBeenCalled();

        await user.click(within(row).getByRole('button', { name: 'Confirm delete memory' }));
        expect(mockedApi.deleteKnowledge).toHaveBeenCalledWith('k1');
    });

    it('drops a deleted reminder from Upcoming and the entity filter', async () => {
        const user = userEvent.setup();
        const reminder = { ...ENTRY, id: 'k1', category: 'reminder', content: 'Dentist Thursday 3:30pm', entity: 'person:Dr. Okonkwo', due_at: '2026-10-08T15:30:00-07:00' };
        mockedApi.getKnowledge.mockResolvedValue({ items: [{ ...ENTRY, content: 'Prefers dark mode' }], total: 1, offset: 0, limit: 25 });
        mockedApi.getUpcomingItems.mockResolvedValueOnce([reminder]);
        mockedApi.getEntities.mockResolvedValueOnce([{ entity: 'person:Dr. Okonkwo', count: 1 }]);
        const row = await renderTable();
        expect(await screen.findByText('Dentist Thursday 3:30pm')).toBeInTheDocument();
        const upcomingCalls = mockedApi.getUpcomingItems.mock.calls.length;
        const entityCalls = mockedApi.getEntities.mock.calls.length;

        await user.click(within(row).getByRole('button', { name: 'Delete memory' }));
        await user.click(within(row).getByRole('button', { name: 'Confirm delete memory' }));

        await waitFor(() => expect(mockedApi.getUpcomingItems.mock.calls.length).toBeGreaterThan(upcomingCalls));
        expect(mockedApi.getEntities.mock.calls.length).toBeGreaterThan(entityCalls);
        await waitFor(() => expect(screen.queryByText('Dentist Thursday 3:30pm')).not.toBeInTheDocument());
    });

    it('refreshes the embedded indicator after an edit', async () => {
        const user = userEvent.setup();
        mockedApi.editKnowledge.mockResolvedValue({ status: 'updated', knowledge_id: 'k1', embedded: true });
        const row = await renderTable();
        expect(await screen.findByText('100% embedded')).toBeInTheDocument();

        mockedApi.getEmbeddingCoverage.mockResolvedValue(coverage(75));
        await user.click(within(row).getByTitle('Edit'));
        await user.click(screen.getByRole('button', { name: 'Update' }));

        expect(await screen.findByText('75% embedded')).toBeInTheDocument();
    });

    it('says when a saved memory could not be embedded', async () => {
        const user = userEvent.setup();
        mockedApi.editKnowledge.mockResolvedValue({
            status: 'updated', knowledge_id: 'k1', embedded: false,
            embed_error: 'Saved, but not embedded: run Maintenance > Rebuild Embeddings.',
        });
        const row = await renderTable();

        await user.click(within(row).getByTitle('Edit'));
        await user.click(screen.getByRole('button', { name: 'Update' }));

        expect(await screen.findByText(/Saved, but not embedded/)).toBeInTheDocument();
        expect(screen.queryByText('Memory updated')).not.toBeInTheDocument();
    });

    it('labels tool stats as excluding memory tools', async () => {
        await renderTable();
        await waitFor(() => expect(screen.getByText('excludes memory tools')).toBeInTheDocument());
        expect(screen.getByText(/excl\. memory/)).toBeInTheDocument();
    });
});

describe('MemoryDashboard layout contract', () => {
    const rule = (selector: string) => {
        const i = dashboardCss.indexOf(`${selector} {`);
        expect(i, `${selector} rule missing`).toBeGreaterThanOrEqual(0);
        return dashboardCss.slice(i, dashboardCss.indexOf('}', i));
    };

    it('sizes the body by the panel, not the window', () => {
        expect(rule('.memory-dashboard-body')).toMatch(/container-type:\s*inline-size/);
    });

    it('lets grid columns shrink so tables scroll in their own box', () => {
        expect(rule('.mem-two-col')).toMatch(/minmax\(0,\s*1fr\)/);
        expect(rule('.mem-two-col > *')).toMatch(/min-width:\s*0/);
        expect(rule('.mem-stat-cards')).toMatch(/minmax\(0,\s*1fr\)/);
    });

    it('pins row actions to the visible edge of the table', () => {
        const css = rule('.mem-knowledge-table .mem-actions-cell');
        expect(css).toMatch(/position:\s*sticky/);
        expect(css).toMatch(/right:\s*0/);
    });
});
