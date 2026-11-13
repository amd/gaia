// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A GFM task list shows checkboxes only, not a bullet beside each box. */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const message: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: '- [x] done task\n- [ ] open task\n\n- plain item\n',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

describe('MessageBubble task lists', () => {
    it('marks checkbox items so the bullet is hidden, and leaves plain items alone', () => {
        const { container } = render(<MessageBubble message={message} />);
        const items = Array.from(container.querySelectorAll('li'));
        const task = items.filter((li) => li.querySelector('input[type="checkbox"]'));
        expect(task).toHaveLength(2);
        for (const li of task) expect(li).toHaveClass('task-list-item');
        const plain = items.find((li) => li.textContent?.includes('plain item'))!;
        expect(plain).not.toHaveClass('task-list-item');
        expect(plain).toHaveClass('md-li');
    });
});
