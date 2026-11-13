// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Copy on an answer puts the text a person sees on the clipboard, not its markdown marks. */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

vi.mock('../../services/api');

const base: Message = {
    id: 2, session_id: 's', role: 'assistant', content: '', created_at: '', rag_sources: null,
};

function copied(message: Message): string {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<MessageBubble message={message} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
    return writeText.mock.calls[0][0];
}

describe('Copy message', () => {
    it('copies an answer without bold marks or list dashes', () => {
        const text = copied({ ...base, content: '- **Sunlight** — light energy\n- **Water** — from the roots' });
        expect(text).toBe('• Sunlight — light energy\n• Water — from the roots');
    });

    it('copies what the user typed exactly', () => {
        const text = copied({ ...base, role: 'user', content: 'make **this** bold' });
        expect(text).toBe('make **this** bold');
    });

    it('leaves out the model reasoning the bubble hides', () => {
        const text = copied({ ...base, content: '<think>plan</think>Answer **hi**' });
        expect(text).toBe('Answer hi');
    });
});
