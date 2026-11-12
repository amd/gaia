// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn that never finished its answer must not be footed "Answered in". */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MessageBubble, splitCutOff, turnEnding } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
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

    it('says a failed turn failed after its time (#7364)', () => {
        const failed = { ...base, content: 'fireworks.no-such-model isn\'t available on Fireworks AI (HTTP 404).', failed: true };
        render(<MessageBubble message={failed} latencyMs={2_000} />);
        expect(screen.getByRole('button', { name: 'Failed after 2 s' })).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('Answered in');
    });

    it('says a saved failure failed when its error is the whole answer', () => {
        const content = 'Error: the model server refused the request.';
        render(<MessageBubble message={{ ...base, content, stats: { place: 'via Fireworks AI' } as Message['stats'] }} latencyMs={2_000} />);
        expect(screen.getByRole('button', { name: 'Failed after 2 s via Fireworks AI' })).toBeInTheDocument();
    });

    it('still says an answered turn answered', () => {
        render(<MessageBubble message={base} latencyMs={16_000} />);
        expect(screen.getByRole('button', { name: 'Answered in 16 s' })).toBeInTheDocument();
    });
});

describe('the server\'s cut-off notice (#6588)', () => {
    const TOOLS = 'Before it stopped, it had started these tools: web_search, write_file.';

    it('splits off the notice and the tools line above it', () => {
        expect(splitCutOff(`Partial.\n\n${TOOLS}\n\n${INTERRUPTED}`)).toEqual({
            body: 'Partial.', notice: 'msg.cutOff.interrupted', tools: 'web_search, write_file',
        });
        expect(splitCutOff('A whole answer.')).toBeNull();
    });

    it('is shown in the chosen language, keeping the answer so far', () => {
        useChatStore.setState({ locale: 'ar' });
        try {
            render(<MessageBubble message={{ ...base, content: `Partial.\n\n${TOOLS}\n\n${INTERRUPTED}` }} />);
            const note = document.querySelector('.msg-cut-off')!;
            expect(note).toHaveTextContent('انقطعت هذه الإجابة عندما أُغلق GAIA.');
            expect(note).toHaveTextContent('قبل أن تتوقف، كانت قد بدأت تشغيل هذه الأدوات:');
            expect(note).toHaveTextContent('web_search, write_file');
            expect(screen.getByText('Partial.')).toBeInTheDocument();
            expect(document.body).not.toHaveTextContent('Send again to retry');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });

    it('offers a labelled Try again when asked to', () => {
        const onTryAgain = vi.fn();
        render(<MessageBubble message={{ ...base, content: INTERRUPTED }} onTryAgain={onTryAgain} />);
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(onTryAgain).toHaveBeenCalledTimes(1);
    });
});
