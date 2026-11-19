// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A hidden sensitive memory in the Advanced knowledge table must not leak its
 * text: not in the row, a tooltip, the expanded detail, or the edit form. The
 * edit pencil stays disabled until the entry is revealed, as on About you.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

const SECRET = 'Bank PIN is 4831';

const ENTRY = {
    id: 'k1', category: 'fact', content: SECRET, domain: null,
    source: 'user', confidence: 0.8, metadata: null, use_count: 0, context: 'global',
    sensitive: true, entity: null, created_at: '2026-10-01T10:00:00+00:00',
    updated_at: '2026-10-01T10:00:00+00:00', last_used: null, due_at: null,
    reminded_at: null, superseded_by: null,
};

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: { total: 1, sensitive_count: 1 }, conversations: {}, tools: { total_calls: 0, unique_tools: 0 },
    });
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [ENTRY], total: 1, offset: 0, limit: 25 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({
        total_items: 1, with_embedding: 1, without_embedding: 0, coverage_pct: 100,
    });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
    mockedApi.editKnowledge.mockResolvedValue({ status: 'updated', knowledge_id: 'k1', embedded: true });
});

/** True if the secret is anywhere a user or assistive tech could read it. */
function secretLeaks(): boolean {
    if (document.body.innerHTML.includes(SECRET)) return true;
    const fields = document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
    return Array.from(fields).some(f => f.value.includes(SECRET));
}

async function renderHiddenRow() {
    render(<MemoryDashboard />);
    fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
    await waitFor(() => expect(mockedApi.getKnowledge).toHaveBeenCalled());
    const reveal = await screen.findByRole('button', { name: /Sensitive — show/ });
    return reveal.closest('tr')!;
}

const editButton = (row: HTMLElement) =>
    within(row).getByRole('button', { name: /Show it first to edit|^Edit$/ });

describe('Advanced knowledge table — hidden sensitive entries', () => {
    it('keeps the text out of the DOM and disables edit while hidden', async () => {
        const row = await renderHiddenRow();

        const pencil = editButton(row);
        expect(pencil).toBeDisabled();
        expect(pencil).toHaveAttribute('title', 'Show it first to edit');
        expect(secretLeaks()).toBe(false);
    });

    it('does not open the editor when the disabled pencil is clicked', async () => {
        const user = userEvent.setup();
        const row = await renderHiddenRow();

        const pencil = editButton(row);
        await user.click(pencil);
        fireEvent.click(pencil);
        pencil.focus();
        await user.keyboard('{Enter}');
        await user.keyboard(' ');

        expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
        expect(document.querySelector('.mem-inline-form')).toBeNull();
        expect(secretLeaks()).toBe(false);
    });

    it('does not show the text when the row is expanded while hidden', async () => {
        const user = userEvent.setup();
        const row = await renderHiddenRow();

        await user.click(within(row).getByText('Fact'));
        expect(row).toHaveAttribute('aria-expanded', 'true');
        expect(await screen.findByText('Full content')).toBeInTheDocument();
        expect(secretLeaks()).toBe(false);
    });

    it('edits normally once revealed', async () => {
        const user = userEvent.setup();
        const row = await renderHiddenRow();

        await user.click(within(row).getByRole('button', { name: /Sensitive — show/ }));
        expect(within(row).getByText(SECRET)).toBeInTheDocument();

        const pencil = editButton(row);
        expect(pencil).toBeEnabled();
        expect(pencil).toHaveAttribute('title', 'Edit');
        await user.click(pencil);

        const editor = document.querySelector<HTMLTextAreaElement>('.mem-inline-form textarea')!;
        expect(editor.value).toBe(SECRET);
        await user.click(screen.getByRole('button', { name: 'Update' }));
        expect(mockedApi.editKnowledge).toHaveBeenCalledWith('k1', expect.objectContaining({ content: SECRET }));
    });

    it('re-hiding disables edit again and closes an open editor', async () => {
        const user = userEvent.setup();
        const row = await renderHiddenRow();

        await user.click(within(row).getByRole('button', { name: /Sensitive — show/ }));
        await user.click(editButton(row));
        expect(document.querySelector('.mem-inline-form')).not.toBeNull();

        // Clicking the revealed text hides it again.
        await user.click(within(row).getByText(SECRET));

        expect(within(row).getByRole('button', { name: /Sensitive — show/ })).toBeInTheDocument();
        expect(editButton(row)).toBeDisabled();
        expect(document.querySelector('.mem-inline-form')).toBeNull();
        expect(secretLeaks()).toBe(false);
    });

    it('marking an entry sensitive while editing it closes the editor', async () => {
        const user = userEvent.setup();
        mockedApi.getKnowledge.mockResolvedValue({
            items: [{ ...ENTRY, sensitive: false }], total: 1, offset: 0, limit: 25,
        });
        render(<MemoryDashboard />);
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
        const row = (await screen.findByText(SECRET)).closest('tr')!;

        await user.click(within(row).getByTitle('Edit'));
        expect(document.querySelector<HTMLTextAreaElement>('.mem-inline-form textarea')!.value).toBe(SECRET);

        mockedApi.getKnowledge.mockResolvedValue({ items: [ENTRY], total: 1, offset: 0, limit: 25 });
        await user.click(within(row).getByTitle('Mark sensitive'));

        expect(mockedApi.editKnowledge).toHaveBeenCalledWith('k1', { sensitive: true });
        await screen.findByRole('button', { name: /Sensitive — show/ });
        expect(document.querySelector('.mem-inline-form')).toBeNull();
        expect(secretLeaks()).toBe(false);
    });

    it('unmarking a revealed entry while editing keeps the editor open', async () => {
        const user = userEvent.setup();
        const row = await renderHiddenRow();

        await user.click(within(row).getByRole('button', { name: /Sensitive — show/ }));
        await user.click(editButton(row));
        mockedApi.getKnowledge.mockResolvedValue({
            items: [{ ...ENTRY, sensitive: false }], total: 1, offset: 0, limit: 25,
        });
        await user.click(within(row).getByTitle('Unmark sensitive'));

        expect(mockedApi.editKnowledge).toHaveBeenCalledWith('k1', { sensitive: false });
        expect(document.querySelector<HTMLTextAreaElement>('.mem-inline-form textarea')!.value).toBe(SECRET);
    });

    it('leaves non-sensitive entries editable', async () => {
        const user = userEvent.setup();
        mockedApi.getKnowledge.mockResolvedValue({
            items: [{ ...ENTRY, sensitive: false, content: 'Prefers dark mode' }], total: 1, offset: 0, limit: 25,
        });
        render(<MemoryDashboard />);
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
        const cell = await screen.findByText('Prefers dark mode');
        const row = cell.closest('tr')!;

        const pencil = within(row).getByTitle('Edit');
        expect(pencil).toBeEnabled();
        await user.click(pencil);
        expect(screen.getByRole('button', { name: 'Update' })).toBeInTheDocument();
    });
});
