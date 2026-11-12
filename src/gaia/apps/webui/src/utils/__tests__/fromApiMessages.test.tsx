// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A stopped answer reloaded from the server still says it was stopped (#6537). */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { fromApiMessages } from '../chatStream';
import { MessageBubble } from '../../components/MessageBubble';

const row = (extra: Record<string, unknown> = {}) => ({
    id: 7, session_id: 's', role: 'assistant', content: 'Fold it twice, then sand it by hand. G',
    created_at: '2026-11-03T00:00:00Z', rag_sources: null, ...extra,
});

describe('fromApiMessages', () => {
    it('marks a reply the server saved as stopped', () => {
        expect(fromApiMessages([row({ stopped: true })])[0].stopState).toBe('stopped');
    });

    it('leaves a finished reply unmarked', () => {
        expect(fromApiMessages([row({ stopped: false })])[0].stopState).toBeUndefined();
        expect(fromApiMessages([row()])[0].stopState).toBeUndefined();
    });

    it('shows the Stopped chip and footer after a reload, not "Answered in"', () => {
        const [message] = fromApiMessages([row({ stopped: true })]);
        render(<MessageBubble message={message} latencyMs={8_000} />);
        expect(screen.getByRole('note')).toHaveTextContent('Stopped');
        expect(screen.getByRole('button', { name: 'Stopped after 8 s' })).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('Answered in');
    });
});
