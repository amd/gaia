// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Copy says Copied only once the clipboard took the text, and says so when it refused.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';
import { log } from '../../utils/logger';

vi.mock('../../services/api');

const answer: Message = {
    id: 2, session_id: 's', role: 'assistant', content: 'Run:\n\n```sh\nnpm test\n```', created_at: '', rag_sources: null,
};

afterEach(() => {
    vi.restoreAllMocks();
});

describe('MessageBubble copy', () => {
    it('shows Copied on the message and the code block when the clipboard accepts', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        render(<MessageBubble message={answer} latest />);

        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
        expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toHaveClass('copied');
        fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
        expect(await screen.findByText('Copied')).toBeInTheDocument();
        expect(writeText).toHaveBeenCalledWith('npm test');
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('says the copy failed, never Copied, when the clipboard refuses', async () => {
        const warn = vi.spyOn(log.ui, 'warn').mockImplementation(() => {});
        Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
        const { container } = render(<MessageBubble message={answer} latest />);

        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
        fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));

        expect(await screen.findAllByRole('alert')).toHaveLength(2);
        expect(container.querySelector('.msg-copy')).toHaveClass('copy-failed');
        expect(container.querySelector('.code-copy')).toHaveClass('copy-failed');
        expect(container.querySelectorAll('.copied')).toHaveLength(0);
        expect(warn).toHaveBeenCalledWith('Could not copy to the clipboard', expect.any(Error));
    });
});
