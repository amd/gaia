// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Characterization tests for MessageBubble's fenced ``email_pre_scan``
 * code-block path: STRUCTURED_FENCE_RE and the EmailPreScanCard mount.
 *
 * These pin TODAY's behavior as a baseline for issue #2109, which is
 * expected to delete/replace this path with the render-registry system
 * (see render-registry.test.tsx). All three tests must pass against the
 * current, unmodified codebase — if this path changes intentionally in the
 * future, update these tests to match the new reality; never edit
 * MessageBubble.tsx to satisfy an assertion written against a stale
 * understanding of it.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const minimalMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: '',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

// Minimal payload satisfying EmailPreScanCard's isPreScanPayload(). The
// angle-bracket sender email is deliberate: it is the reason RenderedContent
// bypasses ReactMarkdown for this fence in the first place (remark-gfm's
// autolink handling of `<email@domain>` corrupts fence detection), so this
// fixture exercises the exact shape the bypass exists for.
const payload = {
    kind: 'email_pre_scan',
    urgent: [{ message_id: 'm1', sender: 'Alice <alice@example.com>', subject: 'Server down' }],
    actionable: [],
    informational_count: 2,
    suggested_archives: [],
    suggested_drafts: [],
};

describe('MessageBubble fenced email_pre_scan characterization', () => {
    it('renders EmailPreScanCard for a fenced email_pre_scan block', () => {
        const content = '```email_pre_scan\n' + JSON.stringify(payload) + '\n```';

        render(<MessageBubble message={{ ...minimalMessage, content }} />);

        expect(screen.getByText('Server down')).toBeInTheDocument();
    });

    it('does not promote bare unfenced JSON to a card (auto-promotion removed, #2109)', () => {
        // No fence markers at all — this used to be auto-wrapped into a
        // fence so EmailPreScanCard would mount (#2109 removed that
        // auto-promotion). The bare JSON now falls through to a plain
        // ReactMarkdown render instead.
        const content = JSON.stringify(payload);

        // A successful render() call below (no uncaught exception) is itself
        // part of the proof this does not crash.
        render(<MessageBubble message={{ ...minimalMessage, content }} />);

        expect(screen.queryByRole('region', { name: 'Inbox pre-scan' })).toBeNull();
    });

    it('falls through to a plain code block for an invalid payload, without crashing', () => {
        // Deliberately missing the required array fields (urgent, actionable,
        // suggested_archives, suggested_drafts, informational_count), so
        // isPreScanPayload returns false. No sender/email field here — this
        // case is isolated from the autolink concern above.
        const invalidPayload = { kind: 'email_pre_scan' };
        const content = '```email_pre_scan\n' + JSON.stringify(invalidPayload) + '\n```';

        // A successful render() call below (no uncaught exception) is itself
        // the proof this does not crash.
        const { container } = render(<MessageBubble message={{ ...minimalMessage, content }} />);

        expect(screen.queryByRole('region', { name: 'Inbox pre-scan' })).toBeNull();
        expect(container.querySelector('.code-block')).not.toBeNull();
    });
});
