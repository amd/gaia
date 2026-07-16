// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for `ConfirmationCard` (issue #2109, Increment 2 — stateless
 * D1: `needs_confirmation` wiring).
 *
 * The module under test does not exist yet:
 *
 *   - src/components/render/ConfirmationCard.tsx
 *
 * This import failure is the expected red — see docs/plans (issue #2109,
 * approved plan) for the full design. These tests pin the frozen contract:
 *
 *   - Renders the machine `action` name (code-derived truth) and the literal
 *     `summary` text, with an "approval needed" visual treatment.
 *   - The humanized label is derived from `action` alone — never by parsing
 *     `summary` — and the raw `action` string is displayed prominently and
 *     visually separated from the summary block (anti-spoof layout).
 *   - `summary` renders as plain React text nodes ONLY. It embeds
 *     attacker-influenced (email-derived) content, so it must never flow
 *     through ReactMarkdown/SafeMarkdown or any dangerouslySetInnerHTML path.
 *   - No generic "Approve"/"Confirm" button exists (V2-10 has no generic
 *     approve path — the canonical event omits args and a token).
 *   - Deny/Dismiss is local component state only — clicking it hides the
 *     card; it does not call out to any store or backend.
 *
 * Component surface: `ConfirmationCard({ data: unknown })`, matching the
 * `CardComponent` shape (`ComponentType<{ data: unknown }>`) used by every
 * other entry in `render/registry.tsx` (TableCard, KeyValueCard, ...), so it
 * can be registered under the `needs_confirmation` key without a bespoke
 * adapter.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmationCard } from '../render/ConfirmationCard';

const basicData = {
    action: 'send_now',
    summary: 'Send the drafted email to alice@example.com now?',
};

describe('ConfirmationCard — action + summary rendering', () => {
    it('renders the literal summary text', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText(basicData.summary)).toBeInTheDocument();
    });

    it('renders the raw machine action name verbatim, prominently and separately from the summary', () => {
        const { container } = render(<ConfirmationCard data={basicData} />);

        // The machine action name is code-derived truth and must appear
        // verbatim somewhere in the card, in an element distinct from the
        // one carrying the summary text (anti-spoof layout requirement).
        const actionEl = container.querySelector('.render-confirmation__action');
        expect(actionEl).not.toBeNull();
        expect(actionEl?.textContent).toBe('send_now');

        const summaryEl = container.querySelector('.render-confirmation__summary');
        expect(summaryEl).not.toBeNull();
        expect(summaryEl).not.toBe(actionEl);
        expect(summaryEl?.contains(actionEl)).toBe(false);
        expect(actionEl?.contains(summaryEl as Node)).toBe(false);
    });

    it('shows an "approval needed" visual treatment', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText(/approval needed/i)).toBeInTheDocument();
    });

    it('wraps output in a .render-confirmation root, consistent with the render/ card family', () => {
        const { container } = render(<ConfirmationCard data={basicData} />);

        expect(container.querySelector('.render-confirmation')).not.toBeNull();
    });
});

describe('ConfirmationCard — humanized label derives from action, not summary', () => {
    it('produces a space-separated (not snake_case) label for a known-shaped action', () => {
        render(<ConfirmationCard data={basicData} />);

        // "send_now" humanized must read as words separated by whitespace,
        // never the raw underscored token, in some label distinct from the
        // verbatim machine-name element.
        expect(screen.getByText(/send\s+now/i)).toBeInTheDocument();
    });

    it('does not derive the label from summary content, even when summary names a different action', () => {
        // A summary that mentions an unrelated action name must not leak
        // into the humanized label — the label is derived from `action`
        // alone (anti-spoof requirement).
        render(
            <ConfirmationCard
                data={{
                    action: 'send_now',
                    summary: 'Consider archive_all_threads before doing anything else.',
                }}
            />,
        );

        expect(screen.getByText(/send\s+now/i)).toBeInTheDocument();
        expect(screen.queryByText(/archive\s+all\s+threads/i)).not.toBeInTheDocument();
    });
});

describe('ConfirmationCard — unknown-action safety', () => {
    const unknownData = {
        action: 'zzz_unregistered_tool_9000',
        summary: 'An action GAIA has never seen before.',
    };

    it('renders without throwing for an action absent from any hardcoded lookup', () => {
        expect(() => render(<ConfirmationCard data={unknownData} />)).not.toThrow();
    });

    it('still displays the raw action verbatim and a humanized (underscore-free) fallback label', () => {
        const { container } = render(<ConfirmationCard data={unknownData} />);

        const actionEl = container.querySelector('.render-confirmation__action');
        expect(actionEl?.textContent).toBe('zzz_unregistered_tool_9000');

        // A generic humanizer must not require a hardcoded per-action entry:
        // some other element must present a readable form with no
        // underscores left over from the raw machine name.
        const labelEl = container.querySelector('.render-confirmation__label');
        expect(labelEl).not.toBeNull();
        expect(labelEl?.textContent).not.toMatch(/_/);
        expect(labelEl?.textContent?.trim().length).toBeGreaterThan(0);
    });
});

describe('ConfirmationCard — metacharacter-literal rendering (rendering-safety mandate)', () => {
    const hostileSummary =
        '<img src=x onerror=alert(1)> **bold** # heading [click me](javascript:alert(1))';

    it('renders hostile summary content as literal text, never as markdown or HTML', () => {
        const { container } = render(
            <ConfirmationCard data={{ action: 'send_now', summary: hostileSummary }} />,
        );

        // No markdown/HTML must have been interpreted: no real <img>, no
        // markdown-bold <strong>, no markdown heading <h1>, no injected
        // <script>, and no live <a> hyperlink from the markdown link syntax.
        expect(container.querySelector('img')).toBeNull();
        expect(container.querySelector('strong')).toBeNull();
        expect(container.querySelector('h1')).toBeNull();
        expect(container.querySelector('script')).toBeNull();
        expect(container.querySelector('a')).toBeNull();

        // The literal source text (markup and all) must still be visible —
        // proving it went through as a plain text node, not stripped either.
        expect(container.textContent).toContain(hostileSummary);
    });

    it('never sets innerHTML from summary — no HTML element created from the ampersand/tag content', () => {
        const trickySummary = '<b>not bold</b> &amp; &lt;script&gt;evil()&lt;/script&gt;';
        const { container } = render(
            <ConfirmationCard data={{ action: 'send_now', summary: trickySummary }} />,
        );

        expect(container.querySelector('b')).toBeNull();
        expect(container.querySelector('script')).toBeNull();
        expect(container.textContent).toContain(trickySummary);
    });
});

describe('ConfirmationCard — no generic approve path (V2-10)', () => {
    it('renders no Approve/Confirm/Allow button — the canonical event has no args or token to act on', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.queryByRole('button', { name: /approve|confirm|allow/i })).not.toBeInTheDocument();
    });
});

describe('ConfirmationCard — dismiss behavior (local state only)', () => {
    it('hides the card content when Deny/Dismiss is clicked, with no confirm-server round trip', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText(basicData.summary)).toBeInTheDocument();

        const dismissBtn = screen.getByRole('button', { name: /deny|dismiss/i });
        fireEvent.click(dismissBtn);

        expect(screen.queryByText(basicData.summary)).not.toBeInTheDocument();
    });
});

describe('ConfirmationCard — invalid payload safety', () => {
    it('renders the invalid-payload fallback (matching other render/ cards) for a malformed payload', () => {
        render(<ConfirmationCard data={{ action: 42 }} />);

        expect(screen.getByText('Invalid needs_confirmation payload')).toBeInTheDocument();
    });
});
