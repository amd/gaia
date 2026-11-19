// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * An answer's actions are a row under its text that a person can see and hit
 * (#7358): always shown under the latest answer, 16 px icons in 28 px
 * targets, and Delete set apart from Copy.
 */

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';
import bubbleCss from '../MessageBubble.css?raw';

vi.mock('../../services/api');

const flat = bubbleCss.replace(/\s+/g, ' ');

const answer: Message = {
    id: 2, session_id: 's', role: 'assistant', content: 'Canberra.', created_at: '', rag_sources: null,
};

describe('answer actions', () => {
    it('marks only the latest answer', () => {
        const { container, rerender } = render(<MessageBubble message={answer} latest />);
        expect(container.querySelector('.msg')?.classList.contains('msg-latest')).toBe(true);
        rerender(<MessageBubble message={answer} />);
        expect(container.querySelector('.msg')?.classList.contains('msg-latest')).toBe(false);
    });

    it('keeps the latest answer\'s actions in view', () => {
        expect(flat).toMatch(/\.msg-latest \.msg-actions \{ opacity: 1; \}/);
    });

    it('sits the row under the text, in the flow', () => {
        expect(flat).toMatch(/\.msg-assistant \.msg-header \{ order: 1;/);
        expect(flat).toMatch(/\.msg-assistant \.msg-actions \{ position: static;/);
    });

    it('draws 16 px icons in 28 px targets, with Delete apart', () => {
        expect(flat).toMatch(/\.msg-actions svg \{ width: 16px; height: 16px; \}/);
        expect(flat).toMatch(/min-width: 28px; min-height: 28px;/);
        expect(flat).toMatch(/\.msg-actions \.msg-delete \{ margin-inline-start: \d+px; \}/);
    });
});
