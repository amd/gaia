// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The Memory page shows the selected agent's memory. gaiacpp keeps its own,
 * smaller store, so the page says whose memory it is and never asks that
 * store for what only the Python agent's has.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';
import { NATIVE_MEMORY_STORE, PYTHON_MEMORY_STORE } from '../../test/memoryStore';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

/** A row as gaiacpp's store serves it: the text, and null where it keeps nothing. */
function nativeRow(over: Record<string, unknown>) {
    return {
        id: 'n', category: 'fact', content: '', domain: null, source: null, confidence: null,
        use_count: null, last_used: null, due_at: null, context: 'global', sensitive: false, entity: null,
        created_at: '2026-11-20T10:00:00+00:00', updated_at: '2026-11-20T10:00:00+00:00', ...over,
    };
}

const ODALYS = nativeRow({ id: 'n1', content: 'Sister Odalys lives in Trondheim' });
const STANDUP = nativeRow({ id: 'n2', category: 'reminder', content: 'Stand-up moves to nine', due_at: '2026-12-01T09:00:00' });

/** Every call the gaiacpp store answers 409 to. */
const PYTHON_ONLY = [
    'getMemoryActivity', 'getEntities', 'getToolSummary', 'getMemoryConversations', 'searchMemoryConversations',
    'getConversationDetail', 'getUpcomingItems', 'getEmbeddingCoverage', 'consolidateSessions', 'rebuildEmbeddings',
    'reconcileMemory', 'rebuildFts', 'refreshSystemContext', 'openDiscoveryStream', 'openInferenceStream',
    'commitDiscovery', 'commitInference', 'clearAllMemory', 'reinitializeMemory', 'listGoals', 'getGoalStats',
] as const;

beforeEach(() => {
    vi.resetAllMocks();
    useChatStore.setState({ settingsSection: null });
    let items = [ODALYS, STANDUP];
    mockedApi.getMemoryStore.mockResolvedValue(NATIVE_MEMORY_STORE);
    mockedApi.getKnowledge.mockImplementation(async () => ({ items, total: items.length, offset: 0, limit: 200 }));
    mockedApi.getMemorySettings.mockResolvedValue({ memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false });
    mockedApi.deleteKnowledge.mockImplementation(async (id: string) => {
        items = items.filter((i) => i.id !== id);
        return { status: 'deleted' };
    });
    mockedApi.editKnowledge.mockResolvedValue({ status: 'updated', knowledge_id: 'n1' });
    mockedApi.createKnowledge.mockResolvedValue({ status: 'created', knowledge_id: 'n9' });
    // The Python store's reads, for the views that show it.
    mockedApi.getMemoryStats.mockResolvedValue(null);
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0 });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
});

const row = (text: string) => screen.getByText(text).closest('li') as HTMLElement;
const banner = () => screen.getByText(/keeps a separate memory/).closest('p') as HTMLElement;

async function openNative() {
    render(<MemoryDashboard />);
    await screen.findByText('Sister Odalys lives in Trondheim');
}

describe('Memory page with gaiacpp selected', () => {
    it('says whose memory it shows, on every tab, and links to the agent setting', async () => {
        await openNative();
        const line = 'Showing what Fast and light remembers. Full-featured keeps a separate memory.';
        expect(banner()).toHaveTextContent(line);

        for (const name of [/Goals/, /Profile setup/, /Advanced/]) {
            fireEvent.click(screen.getByRole('tab', { name }));
            expect(banner()).toHaveTextContent(line);
        }

        fireEvent.click(within(banner()).getByRole('button', { name: 'Change agent in Settings › Agent & model' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('lists what gaiacpp remembers without a due date or a source it never kept', async () => {
        await openNative();

        expect(within(row('Sister Odalys lives in Trondheim')).queryByText(/Picked up in a chat|Saved|You asked GAIA/)).not.toBeInTheDocument();
        // Its store may hold a date-shaped value; without due dates the page shows none.
        expect(within(row('Stand-up moves to nine')).queryByText(/Due /)).not.toBeInTheDocument();
        expect(screen.getByText("Fast and light doesn't keep due dates, so reminders can't be added here.")).toBeInTheDocument();
        expect(screen.getByText("Fast and light doesn't record where a memory came from.")).toBeInTheDocument();

        const kinds = within(screen.getByRole('combobox', { name: 'Kind' })).getAllByRole('option').map((o) => o.textContent);
        expect(kinds).not.toContain('Reminder');
        fireEvent.click(within(row('Stand-up moves to nine')).getByRole('button', { name: /Edit/ }));
        expect(screen.getByRole('textbox', { name: 'Edit memory' })).toBeInTheDocument();
        expect(screen.queryByLabelText('When to remind you')).not.toBeInTheDocument();
    });

    it('replaces Goals, Profile setup and Advanced with one line each, and asks the store for none of them', async () => {
        await openNative();

        fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
        expect(screen.getByText("Goals work on Full-featured's memory and aren't available while Fast and light is your agent.")).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /New goal/i })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: /Profile setup/ }));
        expect(screen.getByText("Profile setup works on Full-featured's memory and isn't available while Fast and light is your agent.")).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Start Scan/i })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
        expect(screen.getByText("The Advanced tools work on Full-featured's memory and aren't available while Fast and light is your agent.")).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Maintenance actions' })).not.toBeInTheDocument();
        expect(screen.queryByText('Knowledge browser')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Delete all' })).not.toBeInTheDocument();
        // Memory on/off works for both agents, so its switch stays.
        expect(screen.getByRole('button', { name: /Remember across chats is on/ })).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Refresh dashboard' }));
        await waitFor(() => expect(mockedApi.getMemoryStore).toHaveBeenCalledTimes(2));
        await screen.findByRole('button', { name: /Remember across chats is on/ });
        for (const call of PYTHON_ONLY) expect(mockedApi[call], call).not.toHaveBeenCalled();
    });

    it('adds, edits, forgets and restores through the knowledge endpoints, sending text only', async () => {
        await openNative();

        fireEvent.change(screen.getByRole('textbox', { name: 'Something GAIA should know about you' }), { target: { value: 'Prefers tea' } });
        fireEvent.click(screen.getByRole('button', { name: /Add/ }));
        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledWith({ content: 'Prefers tea', category: 'fact' }));

        fireEvent.click(within(row('Stand-up moves to nine')).getByRole('button', { name: /Edit/ }));
        fireEvent.change(screen.getByRole('textbox', { name: 'Edit memory' }), { target: { value: 'Stand-up moves to ten' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.editKnowledge).toHaveBeenCalledWith('n2', { content: 'Stand-up moves to ten' }));

        await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Edit memory' })).not.toBeInTheDocument());
        fireEvent.click(within(row('Stand-up moves to nine')).getByRole('button', { name: /Forget/ }));
        await waitFor(() => expect(mockedApi.deleteKnowledge).toHaveBeenCalledWith('n2'));

        fireEvent.click(await screen.findByRole('button', { name: /Undo/ }));
        await waitFor(() => expect(mockedApi.createKnowledge).toHaveBeenCalledTimes(2));
        const restored = mockedApi.createKnowledge.mock.calls[1][0];
        expect(restored).toMatchObject({ content: 'Stand-up moves to nine', category: 'reminder' });
        // gaiacpp answers 422 to a due date, so restoring must not send the one the row carried.
        expect(restored).not.toHaveProperty('due_at');
        for (const call of PYTHON_ONLY) expect(mockedApi[call], call).not.toHaveBeenCalled();
    });

    it('shows the reason when gaiacpp cannot be reached, and nothing from the other store', async () => {
        mockedApi.getKnowledge.mockRejectedValue(new Error('gaiacpp is not running, so its memory cannot be read.'));
        render(<MemoryDashboard />);

        expect(await screen.findByText(/gaiacpp is not running, so its memory cannot be read\./)).toBeInTheDocument();
        expect(banner()).toHaveTextContent('Showing what Fast and light remembers.');
        for (const call of PYTHON_ONLY) expect(mockedApi[call], call).not.toHaveBeenCalled();
    });
});

describe('Memory page with the Python agent selected', () => {
    it('keeps every tab and tool, with no line about agents when there is one memory', async () => {
        mockedApi.getMemoryStore.mockResolvedValue(PYTHON_MEMORY_STORE);
        // The Python store fills in what gaiacpp's leaves null.
        const kept = { source: 'tool', confidence: 0.5, use_count: 0 };
        const items = [{ ...ODALYS, ...kept }, { ...STANDUP, ...kept }];
        mockedApi.getKnowledge.mockResolvedValue({ items, total: 2, offset: 0, limit: 200 });
        await openNative();

        expect(screen.queryByText(/keeps a separate memory/)).not.toBeInTheDocument();
        expect(within(screen.getByRole('combobox', { name: 'Kind' })).getByRole('option', { name: 'Reminder' })).toBeInTheDocument();
        expect(within(row('Stand-up moves to nine')).getByText(/Due /)).toBeInTheDocument();
        expect(within(row('Sister Odalys lives in Trondheim')).getByText(/You asked GAIA to remember this/)).toBeInTheDocument();
        expect(mockedApi.getMemoryActivity).toHaveBeenCalled();
        expect(mockedApi.listGoals).toHaveBeenCalled();

        fireEvent.click(screen.getByRole('tab', { name: /Advanced/ }));
        expect(await screen.findByRole('button', { name: 'Maintenance actions' })).toBeInTheDocument();
        expect(screen.queryByText(/aren't available while Fast and light is your agent/)).not.toBeInTheDocument();
    });

    it('says gaiacpp keeps a separate memory once gaiacpp has one', async () => {
        mockedApi.getMemoryStore.mockResolvedValue({ ...PYTHON_MEMORY_STORE, other_available: true });
        await openNative();

        expect(banner()).toHaveTextContent('Showing what Full-featured remembers. Fast and light keeps a separate memory.');
        expect(within(banner()).getByRole('button', { name: 'Change agent in Settings › Agent & model' })).toBeInTheDocument();
    });

    it('reloads as the other agent when the agent was changed in Settings', async () => {
        mockedApi.getMemoryStore.mockResolvedValue({ ...PYTHON_MEMORY_STORE, other_available: true });
        await openNative();
        expect(mockedApi.listGoals).toHaveBeenCalledTimes(1);

        act(() => useChatStore.getState().openSettings('model'));
        mockedApi.getMemoryStore.mockResolvedValue(NATIVE_MEMORY_STORE);
        act(() => useChatStore.setState({ settingsSection: null }));

        await waitFor(() => expect(banner()).toHaveTextContent('Showing what Fast and light remembers.'));
        await screen.findByText("Fast and light doesn't record where a memory came from.");
        expect(mockedApi.listGoals).toHaveBeenCalledTimes(1);
    });
});

describe('Memory page when it cannot tell whose memory this is', () => {
    it('shows the error and reads no memory at all', async () => {
        mockedApi.getMemoryStore.mockRejectedValue(new Error('Memory store is locked by another process'));
        render(<MemoryDashboard />);

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't tell which agent's memory to show: Memory store is locked by another process");
        expect(screen.queryByText(/keeps a separate memory/)).not.toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'What GAIA knows about you' })).not.toBeInTheDocument();
        expect(mockedApi.getKnowledge).not.toHaveBeenCalled();
        expect(mockedApi.getMemoryStats).not.toHaveBeenCalled();

        mockedApi.getMemoryStore.mockResolvedValue(NATIVE_MEMORY_STORE);
        fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
        expect(await screen.findByText('Sister Odalys lives in Trondheim')).toBeInTheDocument();
    });
});
