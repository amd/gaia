// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { useReadAloudStore } from '../../stores/readAloudStore';
import type { Message } from '../../types';

vi.mock('../../services/api');

const base: Message = {
    id: 7,
    session_id: 's1',
    role: 'assistant',
    content: 'The answer is **42**.',
    created_at: '2026-11-02T10:00:00Z',
    rag_sources: null,
};

const read = vi.fn(async () => undefined);
const stop = vi.fn();

beforeEach(() => {
    read.mockClear();
    stop.mockClear();
    useReadAloudStore.setState({ messageId: null, status: null, error: null, read, stop });
});

describe('Read aloud on a reply', () => {
    it('reads the reply it sits on', () => {
        render(<MessageBubble message={base} />);
        fireEvent.click(screen.getByRole('button', { name: 'Read aloud' }));
        expect(read).toHaveBeenCalledWith('s1:7', 'The answer is **42**.');
    });

    it('says the voice model is downloading under the reply, with its size', () => {
        useReadAloudStore.setState({ messageId: 's1:7', status: 'downloading', downloadGb: 0.35 });
        render(<MessageBubble message={base} />);
        const note = screen.getByRole('status');
        expect(note.textContent).toContain('Downloading the voice model');
        expect(note.textContent).toContain('0.35 GB');
        expect(screen.getByRole('button', { name: 'Stop reading' })).toBeTruthy();
    });

    it('turns into Stop while that reply is being read', () => {
        useReadAloudStore.setState({ messageId: 's1:7', status: 'playing' });
        render(<MessageBubble message={base} />);
        const button = screen.getByRole('button', { name: 'Stop reading' });
        expect(button.getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(button);
        expect(stop).toHaveBeenCalled();
        expect(read).not.toHaveBeenCalled();
    });

    it('is not offered on what the user typed, or while the reply streams', () => {
        const { rerender } = render(<MessageBubble message={{ ...base, role: 'user' }} />);
        expect(screen.queryByRole('button', { name: 'Read aloud' })).toBeNull();
        rerender(<MessageBubble message={base} isStreaming />);
        expect(screen.queryByRole('button', { name: 'Read aloud' })).toBeNull();
    });

    it('says why reading stopped, under that reply only', () => {
        useReadAloudStore.setState({ error: { messageId: 's1:7', kind: 'unreachable', detail: 'Lemonade is down' } });
        render(<MessageBubble message={base} />);
        const alert = screen.getByRole('alert');
        expect(alert.textContent).toContain("the model server isn't answering");
        expect(alert.textContent).toContain('Lemonade is down');
    });

    it('tells the user a code-only reply has nothing to read', () => {
        useReadAloudStore.setState({ error: { messageId: 's1:7', kind: 'nothing', detail: '' } });
        render(<MessageBubble message={base} />);
        expect(screen.getByRole('alert').textContent).toContain('nothing to read aloud here');
    });
});
