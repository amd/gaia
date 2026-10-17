// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The memory page leads with what GAIA knows about the user (#5234): each
 * item can be edited or forgotten in one click, and the engineering stats sit
 * under Advanced. Also covers the dashboard fixes from #4814, #4815 and #4824.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

function row(over: Partial<Record<string, unknown>>) {
    return {
        id: 'k', category: 'fact', content: '', domain: null, source: 'llm_extract',
        confidence: 0.4, metadata: null, use_count: 0, context: 'global', sensitive: false,
        entity: null, created_at: '2026-10-10T10:00:00+00:00', updated_at: '2026-10-10T10:00:00+00:00',
        last_used: null, due_at: null, reminded_at: null, superseded_by: null, ...over,
    };
}

const TEAL = row({ id: 'k1', category: 'preference', content: 'Favourite colour is teal', source: 'tool' });
const CALGARY = row({ id: 'k2', content: 'Lives in Calgary' });
const SECRET = row({ id: 'k5', content: 'Bank PIN hint is the dog', sensitive: true });

const STATS = {
    knowledge: {
        total: 4, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0,
        entity_count: 0, avg_confidence: 0.4, oldest: null, newest: null,
    },
    conversations: { total_turns: 0, total_sessions: 0, first_session: null, last_session: null },
    tools: { total_calls: 4, unique_tools: 2, overall_success_rate: 0.75, total_errors: 1 },
    temporal: { upcoming_count: 0, overdue_count: 0 },
    db_size_bytes: 0,
};

let memoryEnabled = true;

beforeEach(() => {
    vi.resetAllMocks();
    memoryEnabled = true;
    mockedApi.getMemoryStats.mockResolvedValue(STATS);
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [TEAL, CALGARY, SECRET], total: 3, offset: 0, limit: 200 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0 });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockImplementation(async () => ({
        memory_enabled: memoryEnabled, mcp_memory_enabled: false, system_discovery_consent: false,
    }));
    mockedApi.deleteKnowledge.mockResolvedValue({ status: 'deleted' });
    mockedApi.editKnowledge.mockResolvedValue({ status: 'updated', knowledge_id: 'k2' });
    mockedApi.createKnowledge.mockResolvedValue({ status: 'created', knowledge_id: 'k9' });
});

async function renderAbout() {
    render(<MemoryDashboard />);
    await screen.findByText('Lives in Calgary');
}

function itemRow(text: string) {
    return screen.getByText(text).closest('li') as HTMLElement;
}

describe('What GAIA knows about you', () => {
    it('is the first thing the page shows, in plain words', async () => {
        await renderAbout();

        expect(screen.getByRole('heading', { name: 'What GAIA knows about you' })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: /About you/ })).toHaveAttribute('aria-selected', 'true');
        expect(within(screen.getByRole('region', { name: 'Preferences' })).getByText('Favourite colour is teal')).toBeInTheDocument();
        expect(within(itemRow('Lives in Calgary')).getByText(/Picked up in a chat/)).toBeInTheDocument();
        expect(within(itemRow('Favourite colour is teal')).getByText(/You asked GAIA to remember this/)).toBeInTheDocument();
    });

    it('asks the server for rows about the user and leaves out the engineering stats', async () => {
        await renderAbout();

        // Lessons and session summaries are filtered on the server, so paging covers user facts only.
        expect(mockedApi.getKnowledge).toHaveBeenCalledWith(expect.objectContaining({ about_user: true }));
        expect(screen.queryByText('Success Rate')).not.toBeInTheDocument();
        expect(screen.queryByText(/llm_extract/)).not.toBeInTheDocument();
        expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
    });

    it('keeps the stats under Advanced', async () => {
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

        expect(await screen.findByText('Success Rate')).toBeInTheDocument();
    });

    it('forgets an item in one click, and can undo it', async () => {
        await renderAbout();

        fireEvent.click(within(itemRow('Lives in Calgary')).getByRole('button', { name: /Forget/ }));

        await waitFor(() => expect(mockedApi.deleteKnowledge).toHaveBeenCalledWith('k2'));
        const forgotten = (await screen.findByText(/Forgotten:/)).closest('[role="status"]')!;
        expect(forgotten).toHaveTextContent('Lives in Calgary');
        expect(screen.queryByRole('button', { name: /Forget: Lives in Calgary/ })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Undo/ }));

        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledWith(
            expect.objectContaining({ content: 'Lives in Calgary', category: 'fact' }),
        ));
    });

    it('keeps a forgotten item gone when an older load lands afterwards', async () => {
        // The first About load answers at once; the second (from the page's
        // own reload) is held until after the forget, then returns the old list.
        let release: (v: unknown) => void = () => {};
        const held = new Promise(r => { release = r; });
        let aboutLoads = 0;
        const stale = { items: [TEAL, CALGARY, SECRET], total: 3, offset: 0, limit: 200 };
        mockedApi.getKnowledge.mockImplementation(async (params?: memoryApi.KnowledgeListParams) => {
            if (params?.about_user && ++aboutLoads === 2) await held;
            return stale;
        });
        await renderAbout();
        await waitFor(() => expect(aboutLoads).toBe(2));

        fireEvent.click(within(itemRow('Lives in Calgary')).getByRole('button', { name: /Forget/ }));
        await screen.findByText(/Forgotten:/);
        await act(async () => { release(undefined); await held; });

        expect(screen.queryByRole('button', { name: /Forget: Lives in Calgary/ })).not.toBeInTheDocument();
    });

    it('edits an item in place', async () => {
        await renderAbout();

        fireEvent.click(within(itemRow('Lives in Calgary')).getByRole('button', { name: /Edit/ }));
        const box = screen.getByRole('textbox', { name: 'Edit memory' });
        fireEvent.change(box, { target: { value: 'Lives in Edmonton' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(mockedApi.editKnowledge).toHaveBeenCalledWith('k2', { content: 'Lives in Edmonton' }));
    });

    it('adds something the user types', async () => {
        await renderAbout();

        fireEvent.change(screen.getByRole('textbox', { name: /Something GAIA should know/ }), {
            target: { value: 'I prefer short answers' },
        });
        fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), { target: { value: 'preference' } });
        fireEvent.click(screen.getByRole('button', { name: /Add/ }));

        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledWith({
            content: 'I prefer short answers', category: 'preference',
        }));
    });

    it('hides a sensitive item until asked, and never prints it when forgotten', async () => {
        await renderAbout();

        expect(screen.queryByText('Bank PIN hint is the dog')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Forget: sensitive memory' }));

        expect(await screen.findByText(/Forgotten: a sensitive memory/)).toBeInTheDocument();
        expect(screen.queryByText(/Bank PIN/)).not.toBeInTheDocument();
    });

    it('says so when it has nothing yet', async () => {
        mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 200 });
        render(<MemoryDashboard />);

        expect(await screen.findByText("GAIA hasn't saved anything about you yet.")).toBeInTheDocument();
    });

    it('says when memory is off', async () => {
        memoryEnabled = false;
        render(<MemoryDashboard />);

        expect(await screen.findByText(/Memory is off/)).toBeInTheDocument();
    });

    it('says when it could not load, rather than showing an empty list', async () => {
        mockedApi.getKnowledge.mockRejectedValue(new Error('Memory API error (HTTP 500)'));
        render(<MemoryDashboard />);

        expect(await screen.findByText(/Couldn't load your memories/)).toBeInTheDocument();
    });
});

describe('Profile saves refresh the other views (#4814)', () => {
    it('reloads knowledge, stats, upcoming and entities after saving discovery', async () => {
        const es: { onmessage?: (e: { data: string }) => void; close: () => void } = { close: vi.fn() };
        mockedApi.openDiscoveryStream.mockImplementation(() => es as unknown as EventSource);
        mockedApi.commitDiscovery.mockResolvedValue({ stored: 1 });
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Profile setup/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
        act(() => {
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: 'Uses VS Code', category: 'fact', context: 'global', confidence: 0.9 } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'done', total: 1 }) });
        });
        const before = {
            knowledge: mockedApi.getKnowledge.mock.calls.length,
            stats: mockedApi.getMemoryStats.mock.calls.length,
            upcoming: mockedApi.getUpcomingItems.mock.calls.length,
            entities: mockedApi.getEntities.mock.calls.length,
        };

        fireEvent.click(await screen.findByRole('button', { name: /Save Selected/ }));

        await waitFor(() => expect(mockedApi.getMemoryStats.mock.calls.length).toBeGreaterThan(before.stats));
        expect(mockedApi.getKnowledge.mock.calls.length).toBeGreaterThan(before.knowledge);
        expect(mockedApi.getUpcomingItems.mock.calls.length).toBeGreaterThan(before.upcoming);
        expect(mockedApi.getEntities.mock.calls.length).toBeGreaterThan(before.entities);
    });

    it('makes Select All / None keyboard-reachable buttons (#4815)', async () => {
        const es: { onmessage?: (e: { data: string }) => void; close: () => void } = { close: vi.fn() };
        mockedApi.openDiscoveryStream.mockImplementation(() => es as unknown as EventSource);
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Profile setup/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
        act(() => {
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: 'Uses VS Code', category: 'fact', context: 'global', confidence: 0.9 } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'done', total: 1 }) });
        });

        fireEvent.click(await screen.findByRole('button', { name: 'None' }));
        expect(screen.getByRole('checkbox', { name: /Uses VS Code/ })).not.toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: 'Select All' }));
        expect(screen.getByRole('checkbox', { name: /Uses VS Code/ })).toBeChecked();
    });
});

describe('Goals report failures (#4815)', () => {
    const GOAL = {
        id: 'g1', title: 'Ship the report', description: 'Quarterly', status: 'queued' as const,
        source: 'user' as const, mode_required: 'manual' as const, approved_for_auto: false,
        priority: 'medium' as const, progress_notes: '', created_at: '', updated_at: '', tasks: [],
    };

    async function openGoals() {
        mockedApi.listGoals.mockResolvedValue({ goals: [GOAL], total: 1 });
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
        return screen.findByText('Ship the report');
    }

    it('shows a failed cancel instead of doing nothing', async () => {
        mockedApi.cancelGoal.mockRejectedValue(new Error('goal is already running'));
        await openGoals();

        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

        expect(await screen.findByText(/Couldn't cancel the goal: goal is already running/)).toBeInTheDocument();
    });

    it('deletes with an inline second click, never the browser confirm', async () => {
        const confirmSpy = vi.spyOn(window, 'confirm');
        mockedApi.deleteGoal.mockResolvedValue(undefined);
        await openGoals();

        fireEvent.click(screen.getByRole('button', { name: 'Delete goal Ship the report' }));
        expect(mockedApi.deleteGoal).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Delete?' }));

        await waitFor(() => expect(mockedApi.deleteGoal).toHaveBeenCalledWith('g1'));
        expect(confirmSpy).not.toHaveBeenCalled();
    });

    it('expands a goal from the keyboard', async () => {
        await openGoals();
        const header = screen.getByText('Ship the report').closest('[role="button"]') as HTMLElement;

        fireEvent.keyDown(header, { key: 'Enter' });

        expect(header).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText('Quarterly')).toBeInTheDocument();
    });
});

describe('Failures the Advanced view used to hide (#4824)', () => {
    it('shows a failed coverage query as an error', async () => {
        mockedApi.getMemoryStats.mockResolvedValue({
            ...STATS, embedding: null,
            embedding_error: 'Search-index coverage could not be read — see server logs (id=abc).',
        });
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

        expect(await screen.findByRole('alert')).toHaveTextContent('Search-index coverage could not be read');
    });

    it('reports a re-initialize whose system discovery failed', async () => {
        mockedApi.reinitializeMemory.mockResolvedValue({
            cleared: { knowledge: 3, tool_history: 0, conversations: 0 },
            system_context: { stored: 0, error: 'Memory was cleared, but system discovery failed — see server logs (id=xyz).' },
        });
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Re-initialize' }));
        fireEvent.click(screen.getByRole('button', { name: 'Yes, re-initialize' }));

        expect(await screen.findByText(/system discovery failed/)).toBeInTheDocument();
    });
});
