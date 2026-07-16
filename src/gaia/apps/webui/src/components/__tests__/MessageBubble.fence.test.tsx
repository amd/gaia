// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Post-cutover characterization for the retired fence-parsing path (#2109).
 *
 * The pre-#2109 versions of these tests pinned the fence→card hack
 * (STRUCTURED_FENCE_RE, promoteStructuredPayloads, the `pre`-override
 * EmailPreScanCard mount). That path is now DELETED: structured cards
 * arrive exclusively via `tool_result.render` → the card registry
 * (render-registry.test.tsx). What remains to pin is the accepted cost of
 * the cutover — pre-cutover session history containing fenced payloads
 * must degrade gracefully to a plain code block (fenced JSON text), never
 * mount a card, and never crash.
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

// The shape pre-cutover history carries (would have satisfied
// isPreScanPayload back when the fence→card mount existed).
const payload = {
    kind: 'email_pre_scan',
    urgent: [{ message_id: 'm1', sender: 'Alice <alice@example.com>', subject: 'Server down' }],
    actionable: [],
    informational_count: 2,
    suggested_archives: [],
    suggested_drafts: [],
};

describe('retired fence path — pre-cutover history degrades to fenced JSON text (#2109)', () => {
    it('renders a fenced email_pre_scan block as a plain code block, never a card', () => {
        const content = '```email_pre_scan\n' + JSON.stringify(payload) + '\n```';

        const { container } = render(<MessageBubble message={{ ...minimalMessage, content }} />);

        // No card mount — the pre-scan card region must not exist.
        expect(screen.queryByRole('region', { name: 'Inbox pre-scan' })).toBeNull();
        // The payload stays visible as fenced JSON text (a code block).
        expect(container.querySelector('.code-block')).not.toBeNull();
        expect(container.textContent).toContain('"kind"');
    });

    it('leaves bare leading payload JSON as text — no promotion to a card', () => {
        // promoteStructuredPayloads used to wrap this in a fence and mount a
        // card; post-cutover it is ordinary message text.
        const content = JSON.stringify(payload);

        const { container } = render(<MessageBubble message={{ ...minimalMessage, content }} />);

        expect(screen.queryByRole('region', { name: 'Inbox pre-scan' })).toBeNull();
        expect(container.textContent).toContain('email_pre_scan');
    });

    it('does not crash on an ill-formed fenced payload from old history', () => {
        const content = '```email_pre_scan\n{"kind": "email_pre_scan"\n```';

        // A successful render() (no uncaught exception) is the proof.
        const { container } = render(<MessageBubble message={{ ...minimalMessage, content }} />);

        expect(container.querySelector('.code-block')).not.toBeNull();
    });
});
