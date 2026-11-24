// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A queued message's "can't add to this reply" note holds only while that reply runs. */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { QueuedMessages } from '../QueuedMessages';
import { STEER_REFUSED_NOTE, type QueuedMessage } from '../../utils/messageQueue';
import type { MessageKey } from '../../i18n/types';

const NOTE_TEXT = "Can't add to this reply; it will send when the reply ends.";

const refused: QueuedMessage = { id: 1, text: 'second', state: 'queued', note: STEER_REFUSED_NOTE };

function show(items: QueuedMessage[], replying: boolean, busyRetry = false) {
    render(<QueuedMessages items={items} onRemove={() => {}} onRetry={() => {}} replying={replying} busyRetry={busyRetry} />);
}

describe('a queued message the running reply refused', () => {
    it('says so while that reply runs', () => {
        show([refused], true);
        expect(screen.getByText(NOTE_TEXT)).toBeTruthy();
    });

    it('drops the note once the reply has ended and it is next to send', () => {
        show([refused], false);
        expect(screen.queryByText(NOTE_TEXT)).toBeNull();
        expect(screen.getByText('Sending next.')).toBeTruthy();
    });

    it('shows the busy wait, not the stale note, when the chat is still busy', () => {
        show([refused], false, true);
        expect(screen.queryByText(NOTE_TEXT)).toBeNull();
        expect(screen.getByText(/still busy in this chat/)).toBeTruthy();
    });

    it('leaves a message with no note on the plain queued label', () => {
        show([{ id: 2, text: 'third', state: 'queued' }], true);
        expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
    });

    it('treats an empty note as no note', () => {
        show([{ id: 3, text: 'fourth', state: 'queued', note: '' as MessageKey }], true);
        expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
    });
});

describe('the typed text in the strip (#7629)', () => {
    it('keeps its own direction, so an English question ends with its ? in an Arabic window', () => {
        show([{ id: 4, text: 'And what if the rate were 5%?', state: 'queued' }], true);
        expect(screen.getByText('And what if the rate were 5%?').getAttribute('dir')).toBe('auto');
    });
});
