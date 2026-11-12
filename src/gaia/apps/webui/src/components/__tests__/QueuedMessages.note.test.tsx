// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A queued message's "can't add to this reply" note holds only while that reply runs. */

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useChatStore } from '../../stores/chatStore';
import { QueuedMessages } from '../QueuedMessages';
import type { QueuedMessage } from '../../utils/messageQueue';

const STEER_REFUSED_NOTE = "Can't add to this reply; it will send when the reply ends.";

const refused: QueuedMessage = { id: 1, text: 'second', state: 'queued', refused: true };

function show(items: QueuedMessage[], replying: boolean, busyRetry = false) {
    render(<QueuedMessages items={items} onRemove={() => {}} onRetry={() => {}} replying={replying} busyRetry={busyRetry} />);
}

describe('a queued message the running reply refused', () => {
    it('says so while that reply runs', () => {
        show([refused], true);
        expect(screen.getByText(STEER_REFUSED_NOTE)).toBeTruthy();
    });

    it('drops the note once the reply has ended and it is next to send', () => {
        show([refused], false);
        expect(screen.queryByText(STEER_REFUSED_NOTE)).toBeNull();
        expect(screen.getByText('Sending next.')).toBeTruthy();
    });

    it('shows the busy wait, not the stale note, when the chat is still busy', () => {
        show([refused], false, true);
        expect(screen.queryByText(STEER_REFUSED_NOTE)).toBeNull();
        expect(screen.getByText(/still busy in this chat/)).toBeTruthy();
    });

    it('leaves a message with no note on the plain queued label', () => {
        show([{ id: 2, text: 'third', state: 'queued' }], true);
        expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
    });

    it('treats refused: false as not refused', () => {
        show([{ id: 3, text: 'fourth', state: 'queued', refused: false }], true);
        expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
    });
});

describe('the queued-message strip in Arabic (#7629)', () => {
    afterEach(() => useChatStore.setState({ locale: 'en' }));

    it('is translated, and the typed text keeps its own direction', () => {
        useChatStore.setState({ locale: 'ar' });
        render(<QueuedMessages items={[{ id: 4, text: 'And what if the rate were 5%?', state: 'queued' }]}
            onRemove={() => {}} onSteer={() => {}} onRetry={() => {}} replying />);
        expect(screen.getByText(/في قائمة الانتظار/)).toBeTruthy();
        expect(screen.getByRole('button', { name: /أضفها إلى هذا الرد/ })).toBeTruthy();
        expect(screen.getByText('And what if the rate were 5%?').getAttribute('dir')).toBe('auto');
    });
});
