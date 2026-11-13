// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A screen reader hears when a reply arrives or fails, and nothing while it streams. */

import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { TurnAnnouncer } from '../TurnAnnouncer';
import { useChatStore } from '../../stores/chatStore';
import type { Message } from '../../types';

function reply(content: string, extra: Partial<Message> = {}): Message {
    return { id: 2, session_id: 's', role: 'assistant', content, created_at: '', rag_sources: null, ...extra };
}

const status = () => screen.getByRole('status');

describe('turn announcements', () => {
    beforeEach(() => useChatStore.setState({ messages: [] }));

    it('says a reply arrived when the turn ends, not what it says, and stays silent while streaming', () => {
        const { rerender } = render(<TurnAnnouncer busy={false} />);
        rerender(<TurnAnnouncer busy />);
        act(() => useChatStore.setState({ streamingContent: 'Robins, wrens and' } as never));
        expect(status()).toHaveTextContent('');

        useChatStore.setState({ messages: [reply('Robins, wrens and owls.')] });
        rerender(<TurnAnnouncer busy={false} />);
        expect(status()).toHaveTextContent('GAIA has replied.');
        expect(status()).toHaveAttribute('aria-live', 'polite');
    });

    it('announces a failed reply', () => {
        const { rerender } = render(<TurnAnnouncer busy />);
        useChatStore.setState({ messages: [reply('The model server is not answering.', { failed: true })] });
        rerender(<TurnAnnouncer busy={false} />);
        expect(status()).toHaveTextContent("GAIA couldn't finish the reply.");
    });

    it('says nothing when the user stopped the reply themselves', () => {
        const { rerender } = render(<TurnAnnouncer busy />);
        useChatStore.setState({ messages: [reply('Robins', { stopState: 'stopped' })] });
        rerender(<TurnAnnouncer busy={false} />);
        expect(status()).toHaveTextContent('');
    });

    it('says nothing when a refused send ends the turn on the previous reply', () => {
        useChatStore.setState({ messages: [reply('Earlier answer.')] });
        const { rerender } = render(<TurnAnnouncer busy={false} />);
        rerender(<TurnAnnouncer busy />);
        rerender(<TurnAnnouncer busy={false} />);
        expect(status()).toHaveTextContent('');
    });
});
