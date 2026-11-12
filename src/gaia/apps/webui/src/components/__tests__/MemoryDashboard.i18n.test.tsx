// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The Memory dashboard in Arabic (#6590): every tab, its empty, filled, editing
 * and failed states, with nothing left in English outside a name or the gloss.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import { dueLabel } from '../MemoryDashboardAboutYou';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';
import { readsAs } from '../../test/readsAs';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

function row(over: Record<string, unknown>) {
    return {
        id: 'k', category: 'fact', content: '', domain: null, source: 'llm_extract',
        confidence: 0.4, metadata: null, use_count: 0, context: 'global', sensitive: false,
        entity: null, created_at: '2026-10-10T10:00:00+00:00', updated_at: '2026-10-10T10:00:00+00:00',
        last_used: null, due_at: null, reminded_at: null, superseded_by: null, has_embedding: true, ...over,
    };
}

const ITEMS = [
    row({ id: 'k1', category: 'preference', content: 'Prefers short answers', source: 'tool' }),
    row({ id: 'k2', content: 'Lives in Calgary', entity: 'place:calgary' }),
    row({ id: 'k3', category: 'reminder', content: 'Dentist on Friday', due_at: '2026-10-30' }),
    row({ id: 'k4', content: 'Bank PIN hint', sensitive: true }),
];

const STATS = {
    knowledge: {
        total: 4, total_retrievals: 6, by_category: { fact: 2, preference: 1, reminder: 1 }, by_context: { global: 4 },
        sensitive_count: 1, entity_count: 1, avg_confidence: 0.4, oldest: null, newest: null,
    },
    conversations: { total_turns: 3, total_sessions: 1, first_session: null, last_session: null },
    tools: { total_calls: 4, unique_tools: 2, overall_success_rate: 0.75, total_errors: 1 },
    temporal: { upcoming_count: 1, overdue_count: 0 },
    db_size_bytes: 2_400_000,
};

const GOAL = {
    id: 'g1', title: 'Ship the report', description: 'Quarterly numbers', status: 'in_progress' as const,
    source: 'agent_inferred' as const, mode_required: 'manual' as const, approved_for_auto: false,
    priority: 'high' as const, progress_notes: '', created_at: '', updated_at: '',
    tasks: [{ id: 't1', goal_id: 'g1', description: 'Collect the numbers', status: 'blocked', result: null }],
};
const SUGGESTED = { ...GOAL, id: 'g2', title: 'Tidy the inbox', status: 'pending_approval' as const, tasks: [] };

let memoryEnabled = true;

beforeEach(() => {
    vi.resetAllMocks();
    memoryEnabled = true;
    act(() => useChatStore.getState().setLocale('ar'));
    useChatStore.setState({ sessions: [], showMemoryDashboard: true });
    mockedApi.getMemoryStats.mockResolvedValue(STATS as never);
    mockedApi.getMemoryActivity.mockResolvedValue([
        { date: '2026-10-15', conversations: 2, tool_calls: 3, knowledge_added: 1, errors: 1 },
    ]);
    mockedApi.getKnowledge.mockResolvedValue({ items: ITEMS, total: ITEMS.length, offset: 0, limit: 15 } as never);
    mockedApi.getToolSummary.mockResolvedValue([{
        tool_name: 'run_shell', total_calls: 4, success_count: 3, failure_count: 1, denied_count: 0,
        success_rate: 0.75, avg_duration_ms: 1500, last_used: null, last_error: 'Command timed out',
    }] as never);
    mockedApi.getMemoryConversations.mockResolvedValue([{
        session_id: 'deadbeef-1111', turn_count: 3, started_at: null, last_activity: null,
        first_message: 'Remember that I live in Calgary', consolidated: true,
    }] as never);
    mockedApi.getUpcomingItems.mockResolvedValue([
        { id: 'k3', content: 'Dentist on Friday', category: 'reminder', due_at: '2099-10-30T00:00:00Z', reminded_at: null, context: 'work', created_at: '' },
    ] as never);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 4, with_embedding: 3, without_embedding: 1, coverage_pct: 75 });
    mockedApi.getEntities.mockResolvedValue([{ entity: 'place:calgary', count: 1, last_updated: '' }]);
    mockedApi.listGoals.mockResolvedValue({ goals: [GOAL, SUGGESTED], total: 2 } as never);
    mockedApi.getGoalStats.mockResolvedValue({ goals: { in_progress: 1, pending_approval: 1 }, tasks: {} });
    mockedApi.getMemorySettings.mockImplementation(async () => ({
        memory_enabled: memoryEnabled, mcp_memory_enabled: false, system_discovery_consent: false,
    }));
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
});

/** Visible text still in English: not a name in <bdi>, not the English gloss, not a form value. */
function leftoverEnglish(root: HTMLElement = document.body): string[] {
    const left: string[] = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent?.trim() ?? '';
        if (!/[A-Za-z]{3}/.test(text) || /[\u0600-\u06ff]/.test(text)) continue;
        if (node.parentElement?.closest('bdi, .i18n-gloss, code, option, textarea')) continue;
        left.push(text);
    }
    // Hover titles may repeat a memory's own text; labels and hints may not stay English.
    for (const el of root.querySelectorAll('[placeholder], [aria-label]')) {
        for (const name of ['placeholder', 'aria-label']) {
            const value = el.getAttribute(name);
            // "global" is the default context's literal value, shown as the example to type.
            if (!value || value === 'global') continue;
            if (/[A-Za-z]{3}/.test(value) && !/[\u0600-\u06ff]/.test(value)) left.push(`${name}="${value}"`);
        }
    }
    return left;
}

const tab = (name: RegExp) => fireEvent.click(screen.getByRole('tab', { name }));

describe('Memory dashboard in Arabic', () => {
    it('labels the page and every tab in Arabic', async () => {
        render(<MemoryDashboard />);
        await screen.findByText('Lives in Calgary');
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('الذاكرة');
        for (const name of ['عنك', 'الأهداف', 'إعداد الملف الشخصي', 'متقدم']) {
            expect(screen.getByRole('tab', { name: new RegExp(name) })).toBeInTheDocument();
        }
    });

    it('About you: items, groups and the add form', async () => {
        render(<MemoryDashboard />);
        await screen.findByText('Lives in Calgary');
        expect(screen.getByText(readsAs('ما يعرفه GAIA عنك (What GAIA knows about you)'))).toBeInTheDocument();
        expect(screen.getByRole('region', { name: /التفضيلات/ })).toBeInTheDocument();
        expect(within(screen.getByText('Dentist on Friday').closest('li')!).getByText(/موعده/)).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
    });

    it('About you: editing an item', async () => {
        render(<MemoryDashboard />);
        await screen.findByText('Lives in Calgary');
        fireEvent.click(within(screen.getByText('Lives in Calgary').closest('li')!).getByRole('button', { name: /تعديل/ }));
        expect(screen.getByRole('textbox', { name: /تعديل الذكرى/ })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /حفظ/ })).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
    });

    it('About you: empty, off and failed', async () => {
        mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 200 } as never);
        const { unmount } = render(<MemoryDashboard />);
        expect(await screen.findByText(readsAs("لم يحفظ GAIA أي شيء عنك بعد. (GAIA hasn't saved anything about you yet.)"))).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
        unmount();

        memoryEnabled = false;
        const off = render(<MemoryDashboard />);
        expect(await screen.findByText(/الذاكرة متوقفة، لذا/)).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
        off.unmount();

        memoryEnabled = true;
        mockedApi.getKnowledge.mockRejectedValue(new Error('Memory store is locked'));
        render(<MemoryDashboard />);
        const failed = await screen.findByText(/تعذّر تحميل ذكرياتك/);
        // The server's own words stay as sent, in their own bidi island.
        expect(failed.textContent).toContain('\u2068Memory store is locked\u2069');
    });

    it('Advanced: stats, tables, settings and footer', async () => {
        render(<MemoryDashboard />);
        await screen.findByText('Lives in Calgary');
        tab(/متقدم/);
        await screen.findByText('run_shell');
        expect(screen.getByText(readsAs('نسبة النجاح (Success Rate)'))).toBeInTheDocument();
        expect(screen.getByText(readsAs('مفهرس للبحث: 75% (75% embedded)'))).toBeInTheDocument();
        // A size keeps its unit after the number in a right-to-left line.
        expect(screen.getByText('2.3 MB').tagName).toBe('BDI');
        expect(screen.getByText('2.3 MB')).toHaveAttribute('dir', 'ltr');
        expect(leftoverEnglish()).toEqual([]);
    });

    it('Advanced: adding a memory and expanding a row', async () => {
        render(<MemoryDashboard />);
        await screen.findByText('Lives in Calgary');
        tab(/متقدم/);
        await screen.findByText('run_shell');
        fireEvent.click(screen.getByRole('button', { name: /إضافة/ }));
        expect(screen.getByPlaceholderText(/ما الذي ينبغي أن يتذكّره الوكيل/)).toBeInTheDocument();
        fireEvent.click(document.querySelector('.mem-knowledge-table tbody tr') as HTMLElement);
        expect(screen.getByText(readsAs('المحتوى الكامل (Full Content)'))).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
    });

    it('Advanced: the enable-memory dialog', async () => {
        memoryEnabled = false;
        render(<MemoryDashboard />);
        tab(/متقدم/);
        fireEvent.click(await screen.findByRole('button', { name: /الذاكرة متوقفة/ }));
        const dialog = await screen.findByRole('dialog');
        expect(within(dialog).getByText(readsAs('تفعيل ذاكرة الوكيل؟ (Enable Agent Memory?)'))).toBeInTheDocument();
        expect(leftoverEnglish(dialog)).toEqual([]);
    });

    it('Advanced: a failed load', async () => {
        mockedApi.getMemorySettings.mockRejectedValue(new Error('down'));
        mockedApi.getMemoryStats.mockRejectedValue(new Error('down'));
        render(<MemoryDashboard />);
        tab(/متقدم/);
        await screen.findByText(/الذاكرة متوقفة\. شغّلها/);
        expect(leftoverEnglish()).toEqual([]);
    });

    it("Advanced: the server's own error keeps its punctuation in a toast", async () => {
        mockedApi.reinitializeMemory.mockResolvedValue({
            cleared: { knowledge: 3 },
            system_context: { stored: 0, error: 'System discovery failed — see server logs (id=xyz).' },
        } as never);
        render(<MemoryDashboard />);
        tab(/متقدم/);
        await screen.findByText('run_shell');
        fireEvent.click(screen.getByRole('button', { name: /\(Re-initialize\)/ }));
        fireEvent.click(screen.getByRole('button', { name: /\(Yes, re-initialize\)/ }));
        const toast = await screen.findByText(/System discovery failed/);
        expect(toast.textContent).toBe('\u2068System discovery failed — see server logs (id=xyz).\u2069');
    });

    it('Goals: suggested, listed, expanded and the new-goal form', async () => {
        render(<MemoryDashboard />);
        tab(/الأهداف/);
        await screen.findByText('Ship the report');
        expect(screen.getByText(/أهداف اقترحها الوكيل/)).toBeInTheDocument();
        fireEvent.click(screen.getByText('Ship the report').closest('[role="button"]') as HTMLElement);
        expect(screen.getByText(readsAs('متعطّل (blocked)'))).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /هدف جديد/ }));
        expect(screen.getByPlaceholderText(/عنوان الهدف/)).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);
    });

    it('Goals: empty', async () => {
        mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
        render(<MemoryDashboard />);
        tab(/الأهداف/);
        await screen.findByText(/لا توجد أهداف بعد/);
        expect(leftoverEnglish()).toEqual([]);
    });

    it('Profile setup: idle, then a run with findings to review', async () => {
        const es = { onmessage: null as ((e: { data: string }) => void) | null, onerror: null as (() => void) | null, close: vi.fn() };
        mockedApi.openDiscoveryStream.mockReturnValue(es as never);
        render(<MemoryDashboard />);
        tab(/إعداد الملف الشخصي/);
        expect(screen.getByRole('button', { name: /ابدأ الفحص/ })).toBeInTheDocument();
        expect(leftoverEnglish()).toEqual([]);

        fireEvent.click(screen.getByRole('button', { name: /ابدأ الفحص/ }));
        act(() => {
            es.onmessage!({ data: JSON.stringify({ type: 'log', message: 'Scanning installed apps' }) });
            es.onmessage!({ data: JSON.stringify({ type: 'finding', item: { content: 'Uses VS Code', category: 'fact', context: 'global', confidence: 0.9 } }) });
            es.onmessage!({ data: JSON.stringify({ type: 'done', total: 1 }) });
        });
        expect(await screen.findByText(/المراجعة والحفظ/)).toBeInTheDocument();
        expect(screen.getByText('Scanning installed apps').tagName).toBe('BDI');
        expect(leftoverEnglish()).toEqual([]);
    });
});

describe('Memory dashboard strings outside React', () => {
    it('writes a due date in the chosen language, English by default', () => {
        const now = new Date(2026, 9, 20, 12);
        expect(dueLabel('2026-10-22', now)?.text).toMatch(/^Due /);
        expect(stripIsolates(dueLabel('2026-10-22', now, translator('ar'))!.text)).toMatch(/^موعده .* \(Due /);
    });
});
