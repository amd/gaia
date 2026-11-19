// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The memory page leads with what GAIA knows about the user (#5234): each
 * item can be edited or forgotten in one click, and the engineering stats sit
 * under Advanced. Also covers the dashboard fixes from #4814, #4815 and #4824.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import { dueLabel, MemoryDashboardAboutYou } from '../MemoryDashboardAboutYou';
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
    // A store, not a fixed list: a forget's refresh must not bring the item back.
    let items = [TEAL, CALGARY, SECRET];
    mockedApi.getKnowledge.mockImplementation(async () => ({ items, total: items.length, offset: 0, limit: 200 }));
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
    mockedApi.deleteKnowledge.mockImplementation(async (id: string) => {
        items = items.filter((i) => i.id !== id);
        return { status: 'deleted' };
    });
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
        expect(screen.queryByText('Success rate')).not.toBeInTheDocument();
        expect(screen.queryByText(/llm_extract/)).not.toBeInTheDocument();
        expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
    });

    it('keeps the stats under Advanced', async () => {
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));

        expect(await screen.findByText('Success rate')).toBeInTheDocument();
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

    it('Enter that picks an IME candidate does not save the edit', async () => {
        await renderAbout();

        fireEvent.click(within(itemRow('Lives in Calgary')).getByRole('button', { name: /Edit/ }));
        const box = screen.getByRole('textbox', { name: 'Edit memory' });
        fireEvent.change(box, { target: { value: 'にほ' } });
        fireEvent.keyDown(box, { key: 'Enter', isComposing: true });

        expect(mockedApi.editKnowledge).not.toHaveBeenCalled();
        expect(screen.getByRole('textbox', { name: 'Edit memory' })).toBeInTheDocument();
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

    it('uses a placeholder short enough to read in the add box', async () => {
        await renderAbout();

        const placeholder = screen.getByRole('textbox', { name: /Something GAIA should know/ }).getAttribute('placeholder') ?? '';
        expect(placeholder).toBe('Tell GAIA something about you');
    });

    it('hides a sensitive item until asked, and never prints it when forgotten', async () => {
        await renderAbout();

        expect(screen.queryByText('Bank PIN hint is the dog')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Forget: sensitive memory' }));

        expect(await screen.findByText(/Forgotten: a sensitive memory/)).toBeInTheDocument();
        expect(screen.queryByText(/Bank PIN/)).not.toBeInTheDocument();
    });

    it('keeps a sensitive item hidden from its edit button until it is shown', async () => {
        await renderAbout();

        const edit = screen.getByRole('button', { name: 'Edit: sensitive memory' });
        expect(edit).toBeDisabled();
        fireEvent.click(edit);
        expect(screen.queryByRole('textbox', { name: 'Edit memory' })).not.toBeInTheDocument();
        expect(screen.queryByDisplayValue(/Bank PIN/)).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Sensitive — show/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Edit: Bank PIN hint is the dog' }));
        expect(screen.getByRole('textbox', { name: 'Edit memory' })).toHaveValue('Bank PIN hint is the dog');
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

    it('says how many there are past the first page and shows the rest on request', async () => {
        // v0.26.0-rc18: 323 memories, 200 rows, no count and no way to the other 123.
        const all = Array.from({ length: 323 }, (_, i) => row({ id: `m${i}`, content: `Memory number ${i}` }));
        mockedApi.getKnowledge.mockImplementation(async (params = {}) => {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? 50;
            return { items: all.slice(offset, offset + limit), total: all.length, offset, limit };
        });
        render(<MemoryDashboard />);
        await screen.findByText('Memory number 0');

        expect(screen.getAllByRole('button', { name: /^Forget:/ })).toHaveLength(200);
        expect(screen.getByText('Showing 200 of 323 memories')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Show more' }));

        expect(await screen.findByText('Memory number 322')).toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: /^Forget:/ })).toHaveLength(323);
        expect(screen.queryByText(/^Showing \d+ of/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
        for (const call of mockedApi.getKnowledge.mock.calls) {
            expect(call[0]?.limit ?? 0).toBeLessThanOrEqual(200);
        }
    });

    it('keeps the extra rows in view after forgetting one of them', async () => {
        let all = Array.from({ length: 250 }, (_, i) => row({ id: `m${i}`, content: `Memory number ${i}` }));
        mockedApi.getKnowledge.mockImplementation(async (params = {}) => {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? 50;
            return { items: all.slice(offset, offset + limit), total: all.length, offset, limit };
        });
        mockedApi.deleteKnowledge.mockImplementation(async (id: string) => {
            all = all.filter((i) => i.id !== id);
            return { status: 'deleted' };
        });
        render(<MemoryDashboard />);
        await screen.findByText('Memory number 0');
        fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
        await screen.findByText('Memory number 249');

        fireEvent.click(within(itemRow('Memory number 240')).getByRole('button', { name: /^Forget:/ }));

        await waitFor(() => expect(screen.queryByText('Memory number 240')).not.toBeInTheDocument());
        expect(screen.getByText('Memory number 249')).toBeInTheDocument();
    });

    it('shows a row once when a memory is saved between two page loads', async () => {
        let all = Array.from({ length: 250 }, (_, i) => row({ id: `m${i}`, content: `Memory number ${i}` }));
        let saved = false;
        mockedApi.getKnowledge.mockImplementation(async (params = {}) => {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? 50;
            if (offset > 0 && !saved) {
                // The agent saves a memory; it sorts to the top and shifts every row down one.
                saved = true;
                all = [row({ id: 'fresh', content: 'Just saved' }), ...all];
            }
            return { items: all.slice(offset, offset + limit), total: all.length, offset, limit };
        });
        render(<MemoryDashboard />);
        await screen.findByText('Memory number 0');
        fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
        await screen.findByText('Memory number 249');

        expect(screen.getAllByText('Memory number 199')).toHaveLength(1);
    });

    it('keeps the extra rows in view when an add reloads the list', async () => {
        const all = Array.from({ length: 250 }, (_, i) => row({ id: `m${i}`, content: `Memory number ${i}` }));
        mockedApi.getKnowledge.mockImplementation(async (params = {}) => {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? 50;
            return { items: all.slice(offset, offset + limit), total: all.length, offset, limit };
        });
        render(<MemoryDashboard />);
        await screen.findByText('Memory number 0');
        fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
        await screen.findByText('Memory number 249');

        fireEvent.change(screen.getByRole('textbox', { name: /Something GAIA should know/ }), {
            target: { value: 'I prefer short answers' },
        });
        fireEvent.click(screen.getByRole('button', { name: /Add/ }));
        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalled());
        const pageReads = () => mockedApi.getKnowledge.mock.calls
            .map(([p]) => p)
            .filter((p) => p?.about_user && p?.offset === 200);
        // Show more read the second page once; the reload after the add reads it again.
        await waitFor(() => expect(pageReads()).toHaveLength(2));

        expect(screen.getByText('Memory number 249')).toBeInTheDocument();
        expect(screen.queryByText(/^Showing \d+ of/)).not.toBeInTheDocument();
    });

    it('still shows the next page when a reload lands during Show more', async () => {
        const all = Array.from({ length: 250 }, (_, i) => row({ id: `m${i}`, content: `Memory number ${i}` }));
        let release: (() => void) | null = null;
        mockedApi.getKnowledge.mockImplementation(async (params = {}) => {
            const offset = params.offset ?? 0;
            const limit = params.limit ?? 50;
            if (offset === 200 && !release) {
                // Hold the first read of page 2 until the parent's reload has started.
                await new Promise<void>((resolve) => { release = resolve; });
            }
            return { items: all.slice(offset, offset + limit), total: all.length, offset, limit };
        });
        const props = {
            memoryEnabled: true,
            onChanged: () => {},
            onOpenAdvanced: () => {},
            showToast: () => {},
        };
        const { rerender } = render(<MemoryDashboardAboutYou {...props} reloadKey={0} />);
        await screen.findByText('Memory number 0');

        fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
        await waitFor(() => expect(release).not.toBeNull());
        rerender(<MemoryDashboardAboutYou {...props} reloadKey={1} />);
        await act(async () => { release?.(); });

        expect(await screen.findByText('Memory number 249')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
    });
});

describe('Profile saves refresh the other views (#4814)', () => {
    it('reloads knowledge, stats, upcoming and entities after saving discovery', async () => {
        const es: { onmessage?: (e: { data: string }) => void; close: () => void } = { close: vi.fn() };
        mockedApi.openDiscoveryStream.mockImplementation(() => es as unknown as EventSource);
        mockedApi.commitDiscovery.mockResolvedValue({ stored: 1 });
        await renderAbout();
        fireEvent.click(screen.getByRole('tab', { name: /Profile setup/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Start scan' }));
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
        fireEvent.click(screen.getByRole('button', { name: 'Start scan' }));
        act(() => {
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: 'Uses VS Code', category: 'fact', context: 'global', confidence: 0.9 } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'done', total: 1 }) });
        });

        fireEvent.click(await screen.findByRole('button', { name: 'None' }));
        expect(screen.getByRole('checkbox', { name: /Uses VS Code/ })).not.toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
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

describe('Reminder due dates', () => {
    const NOW = new Date(2026, 9, 22, 12, 0);
    // Stored like the memory store writes it: an ISO timestamp with an offset.
    const localMidnight = (day: number) => new Date(2026, 9, day).toISOString();

    beforeEach(() => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(NOW);
    });
    afterEach(() => { vi.useRealTimers(); });

    it('shows the stored date, not just the words the user said', async () => {
        const interview = row({
            id: 'r1', category: 'reminder', content: "I'm interviewing at Contoso next Friday",
            due_at: localMidnight(30), updated_at: NOW.toISOString(),
        });
        mockedApi.getKnowledge.mockResolvedValue({ items: [CALGARY, interview], total: 2, offset: 0, limit: 200 });
        await renderAbout();

        const due = within(itemRow("I'm interviewing at Contoso next Friday")).getByText(/^Due /);
        expect(due).toHaveTextContent(/Fri/);
        expect(due).toHaveTextContent(/Oct/);
        expect(due).toHaveTextContent(/30/);
        expect(due).not.toHaveClass('is-overdue');
        expect(within(itemRow('Lives in Calgary')).queryByText(/Due /)).not.toBeInTheDocument();
    });

    it('says it was due once the date has passed', async () => {
        const dentist = row({ id: 'r2', category: 'reminder', content: 'Dentist on Friday', due_at: localMidnight(16) });
        mockedApi.getKnowledge.mockResolvedValue({ items: [CALGARY, dentist], total: 2, offset: 0, limit: 200 });
        await renderAbout();

        const due = within(itemRow('Dentist on Friday')).getByText(/^Was due /);
        expect(due).toHaveTextContent(/Fri.*Oct.*16|Fri.*16.*Oct/);
        expect(due).toHaveClass('is-overdue');
    });

    it('counts a whole-day reminder as due, not past, on its own day', () => {
        expect(dueLabel(localMidnight(22), NOW)).toMatchObject({ text: expect.stringMatching(/^Due /), past: false });
        expect(dueLabel('2026-10-21', NOW)).toMatchObject({ text: expect.stringMatching(/^Was due .*21/), past: true });
        expect(dueLabel(new Date(2026, 9, 22, 9, 30).toISOString(), NOW)).toMatchObject({
            text: expect.stringMatching(/^Was due /), past: true,
        });
        expect(dueLabel('not a date', NOW)).toBeNull();
    });

    it('asks when to remind, and saves that time with a reminder added here', async () => {
        await renderAbout();

        fireEvent.change(screen.getByRole('textbox', { name: /Something GAIA should know/ }), {
            target: { value: 'Water the plants' },
        });
        expect(screen.queryByLabelText('When to remind you')).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), { target: { value: 'reminder' } });
        const add = screen.getByRole('button', { name: /Add/ });
        expect(add).toBeDisabled();

        fireEvent.change(screen.getByLabelText('When to remind you'), { target: { value: '2026-10-25T18:00' } });
        expect(add).toBeEnabled();
        fireEvent.click(add);

        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledWith({
            content: 'Water the plants', category: 'reminder', due_at: '2026-10-25T18:00',
        }));
    });

    it('lets a reminder\'s time be changed when editing it', async () => {
        const plants = row({ id: 'r3', category: 'reminder', content: 'Water the plants', due_at: new Date(2026, 9, 25, 18, 0).toISOString() });
        mockedApi.getKnowledge.mockResolvedValue({ items: [CALGARY, plants], total: 2, offset: 0, limit: 200 });
        await renderAbout();

        fireEvent.click(within(itemRow('Water the plants')).getByRole('button', { name: /Edit/ }));
        const when = screen.getByLabelText('When to remind you');
        expect(when).toHaveValue('2026-10-25T18:00');
        fireEvent.change(when, { target: { value: '2026-10-26T09:30' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(mockedApi.editKnowledge).toHaveBeenCalledWith('r3', { due_at: '2026-10-26T09:30' }));
    });

    it('keeps the due date when a forgotten reminder is undone', async () => {
        const interview = row({ id: 'r1', category: 'reminder', content: 'Interview at Contoso', due_at: localMidnight(30) });
        mockedApi.getKnowledge.mockResolvedValue({ items: [CALGARY, interview], total: 2, offset: 0, limit: 200 });
        await renderAbout();

        fireEvent.click(within(itemRow('Interview at Contoso')).getByRole('button', { name: /Forget/ }));
        fireEvent.click(await screen.findByRole('button', { name: /Undo/ }));

        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledWith(
            expect.objectContaining({ content: 'Interview at Contoso', due_at: localMidnight(30) }),
        ));
    });
});
