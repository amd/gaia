// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "This turn" gets its own heading only when sources or files sit beside it;
// alone under "This answer" it would just repeat the heading above.

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThisAnswer } from '../SideRail';
import * as api from '../../services/api';
import type { Message as ChatMessage, TurnSummary } from '../../types';

vi.mock('../../services/api');

const BARE: TurnSummary = {
    pages: [],
    documents: [],
    files: [],
    sites: [],
    live_data: { attempted: 0, failed: 0 },
    cost: { kind: 'local' },
    model: { remote: false, provider: 'lemonade', provider_name: 'Lemonade', name: 'Qwen3.6-35B-A3B-GGUF' },
} as TurnSummary;

const turn = (s: TurnSummary): ChatMessage[] => [
    { id: 1, session_id: 's', role: 'user', content: 'q', created_at: '', rag_sources: null },
    { id: 2, session_id: 's', role: 'assistant', content: 'a', created_at: '', rag_sources: null, turnSummary: s },
] as ChatMessage[];

beforeEach(() => {
    vi.mocked(api.getFileStatus).mockResolvedValue({ files: [] });
});

describe('the This turn heading', () => {
    it('is left out when the turn facts are all there is', () => {
        render(<ThisAnswer messages={turn(BARE)} />);

        expect(screen.queryByRole('button', { name: 'This turn' })).not.toBeInTheDocument();
        expect(screen.getByText('Model')).toBeInTheDocument();
        expect(screen.getByText('Websites')).toBeInTheDocument();
    });

    it('stays when sources are listed beside the facts', () => {
        const withDoc = {
            ...BARE,
            documents: [{ name: 'report.pdf', path: 'C:\\docs\\report.pdf', pages: [2], passages: 1 }],
        } as TurnSummary;
        render(<ThisAnswer messages={turn(withDoc)} />);

        expect(screen.getByRole('button', { name: 'This turn' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Sources' })).toBeInTheDocument();
    });
});
