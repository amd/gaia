// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The "This chat" panel in Arabic: each label bracketed once with its English,
// provider names and paths kept intact, and English unchanged.

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ThisAnswer } from '../SideRail';
import { whenText } from '../session-panel/OutputsSection';
import { totalText } from '../session-panel/ChangesSection';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { SessionChanges } from '../../types/sessionPanel';
import type { Message as ChatMessage, TurnSummary } from '../../types';

vi.mock('../../services/api');

const ar = translator('ar');
const plain = (text: string) => (content: string) => stripIsolates(content) === text;

const SUMMARY: TurnSummary = {
    pages: [],
    documents: [{ name: 'annual-report-2025-ar.pdf', path: 'C:\\docs\\annual-report-2025-ar.pdf', pages: [5, 6, 8], passages: 6 }],
    files: [],
    sites: [],
    live_data: { attempted: 0, failed: 0 },
    cost: { kind: 'estimate', usd: 0.0023 },
    model: { remote: true, provider: 'fireworks', provider_name: 'Fireworks AI', name: 'glm-5p3-flash' },
} as TurnSummary;

const turn = (s: TurnSummary): ChatMessage[] => [
    { id: 1, session_id: 's', role: 'user', content: 'q', created_at: '', rag_sources: null },
    { id: 2, session_id: 's', role: 'assistant', content: 'a', created_at: '', rag_sources: null, turnSummary: s },
] as ChatMessage[];

beforeEach(() => {
    vi.mocked(api.getFileStatus).mockResolvedValue({ files: [] });
});
afterEach(() => act(() => useChatStore.getState().setLocale('en')));

describe('This answer', () => {
    it('reads as before in English', () => {
        render(<ThisAnswer messages={turn(SUMMARY)} />);
        for (const text of [
            'Sources', 'pp. 5, 6, 8 · 6 passages', 'This turn', 'Model', 'Fireworks AI (cloud)', 'Documents',
            'Searched on this PC. The passages used were sent to Fireworks AI.', 'Websites',
            "None from GAIA's web tools", 'Cost', 'About $0.0023 at list price',
        ]) expect(screen.getByText(text)).toBeInTheDocument();
    });

    it('reads in Arabic, each line bracketed once, names kept whole', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<ThisAnswer messages={turn(SUMMARY)} />);
        for (const text of [
            'المصادر (Sources)',
            'الصفحات 5, 6, 8 · المقاطع: 6 (pp. 5, 6, 8 · 6 passages)',
            'هذا الدور (This turn)',
            'Fireworks AI (سحابي) (Fireworks AI (cloud))',
            'جرى البحث على هذا الجهاز. وأُرسلت المقاطع المستخدمة إلى Fireworks AI. (Searched on this PC. The passages used were sent to Fireworks AI.)',
            "لا شيء من أدوات الويب في GAIA (None from GAIA's web tools)",
            'نحو $0.0023 بسعر القائمة (About $0.0023 at list price)',
        ]) expect(screen.getByText(plain(text))).toBeInTheDocument();
    });

    it('isolates the provider name inside the Arabic sentence', () => {
        expect(ar('rail.docsSent', { provider: 'Fireworks AI' })).toContain('\u2068Fireworks AI\u2069');
    });
});

describe('panel helpers', () => {
    it('keep their English output and gain Arabic', () => {
        const now = Date.parse('2026-01-01T00:10:00Z');
        expect(whenText('2026-01-01 00:07:00', now)).toBe('3 min ago');
        expect(stripIsolates(whenText('2026-01-01 00:07:00', now, ar))).toBe('قبل 3 د (3 min ago)');
        const changes = { totals: { files: 4, additions: 120, deletions: 8, uncounted: 0 } } as unknown as SessionChanges;
        expect(totalText(changes)).toBe('4 files changed, +120 −8');
        expect(stripIsolates(totalText(changes, ar))).toBe('الملفات: 4 تغيّرت، +120 −8 (4 files changed, +120 −8)');
    });
});
