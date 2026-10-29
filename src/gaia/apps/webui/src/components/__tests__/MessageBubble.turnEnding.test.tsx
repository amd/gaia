// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn that never finished its answer must not be footed "Answered in". */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble, turnEnding } from '../MessageBubble';
import type { Message } from '../../types';

const INTERRUPTED = 'This answer was interrupted when GAIA closed. Send again to retry.';

const base: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: 'Ulaanbaatar.',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

describe('turnEnding', () => {
    it('is stopped for a turn the user stopped', () => {
        expect(turnEnding('partial', 'stopped')).toBe('stopped');
    });

    it('is interrupted for a turn closed with a cut-off notice', () => {
        expect(turnEnding(`The first paragraph.\n\n${INTERRUPTED}`, undefined)).toBe('interrupted');
    });

    it('is undefined for an answer', () => {
        expect(turnEnding('Ulaanbaatar.', undefined)).toBeUndefined();
    });
});

describe('MessageBubble footer', () => {
    it('says a stopped turn stopped after its time', () => {
        render(<MessageBubble message={{ ...base, content: 'Cancelled.' }} latencyMs={16_000} />);
        expect(screen.getByRole('button', { name: 'Stopped after 16 s' })).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('Answered in');
    });

    it('says a turn cut off when GAIA closed was interrupted', () => {
        render(<MessageBubble message={{ ...base, content: INTERRUPTED }} latencyMs={16_000} />);
        expect(screen.getByRole('button', { name: 'Interrupted after 16 s' })).toBeInTheDocument();
    });

    it('still says an answered turn answered', () => {
        render(<MessageBubble message={base} latencyMs={16_000} />);
        expect(screen.getByRole('button', { name: 'Answered in 16 s' })).toBeInTheDocument();
    });
});
