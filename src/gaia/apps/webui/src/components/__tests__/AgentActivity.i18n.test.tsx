// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The timeline in Arabic: each line written once in Arabic and bracketed once
// with the English, and English byte-identical to what it was before.

import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity, stepsSummary, thoughtLabel, toolRowText, type ActivityRow } from '../AgentActivity';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const ar = translator('ar');
const plain = (text: string) => stripIsolates(text);

function tool(id: number, name: string, opts: Partial<AgentStep> = {}): ActivityRow {
    return { id, type: 'tool', label: 'Using tool', tool: name, active: false, success: true, result: 'ok', timestamp: id, ...opts };
}

afterEach(() => act(() => useChatStore.getState().setLocale('en')));

describe('folded-run summary', () => {
    const rows = [tool(1, 'index_documents'), tool(2, 'query_specific_file'), tool(3, 'query_specific_file')];

    it('stays byte-identical in English', () => {
        expect(stepsSummary(rows)).toBe('Indexed documents, queried file ×2');
    });

    it('reads in Arabic with the whole English line in brackets once', () => {
        expect(plain(stepsSummary(rows, ar))).toBe('فهرس المستندات، استعلم في ملف ×2 (Indexed documents, queried file ×2)');
    });

    it('says how many more kinds there were', () => {
        const many = [
            tool(1, 'index_documents'), tool(2, 'query_documents'), tool(3, 'read_file'),
            tool(4, 'web_search'), { id: 5, type: 'error', label: 'x', timestamp: 5 } as ActivityRow,
        ];
        expect(stepsSummary(many)).toBe('Indexed documents, queried documents, read file and 2 more');
        expect(plain(stepsSummary(many, ar)))
            .toBe('فهرس المستندات، استعلم في المستندات، قرأ ملفًا و2 أخرى (Indexed documents, queried documents, read file and 2 more)');
    });

    it('names plans, errors, policy blocks and pure reasoning', () => {
        expect(plain(stepsSummary([{ id: 1, type: 'plan', label: 'p', timestamp: 1 } as ActivityRow], ar)))
            .toBe('وضع خطة (Made a plan)');
        expect(plain(stepsSummary([{ id: 1, type: 'policy_alert', label: 'p', timestamp: 1 } as ActivityRow], ar)))
            .toBe('محظور بموجب السياسة (Blocked by policy)');
        expect(plain(stepsSummary([], ar))).toBe('فكّر في الأمر (Thought it through)');
    });

    it('names a skill the agent picked up', () => {
        const skill = tool(1, 'load_skill', { narration: 'Loading skill: meeting-notes' });
        expect(stepsSummary([skill])).toBe('Used meeting-notes skill');
        expect(plain(stepsSummary([skill], ar))).toBe('استخدم مهارة meeting-notes (Used meeting-notes skill)');
    });
});

describe('thought label', () => {
    it.each([
        [true, undefined, 'جارٍ التفكير (Thinking)'],
        [false, 8_400, 'فكّر لمدة 8s (Thought for 8s)'],
        [false, 400, 'فكّر (Thought)'],
    ])('live %s, %s ms', (live, ms, label) => {
        expect(plain(thoughtLabel(live, ms, ar))).toBe(label);
    });
});

describe('tool rows', () => {
    it('says a failed call failed, in one bracket', () => {
        const failed = tool(1, 'query_documents', { success: false, result: 'timeout' });
        expect(toolRowText(failed)).toBe('Querying documents failed');
        expect(plain(toolRowText(failed, ar))).toBe('تعذّر: الاستعلام في المستندات (Querying documents failed)');
    });

    it('labels a tool it has no entry for by its name', () => {
        expect(plain(toolRowText(tool(1, 'make_chart', { active: true, success: undefined, result: undefined }), ar)))
            .toBe('استخدام make chart (Using make chart)');
    });

    it('renders the folded summary in Arabic in the timeline', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<AgentActivity steps={[tool(1, 'index_documents'), tool(2, 'query_specific_file')]} isActive={false} />);
        expect(screen.getByText((content) => plain(content) === 'فهرس المستندات، استعلم في ملف (Indexed documents, queried file)'))
            .toBeInTheDocument();
    });
});
