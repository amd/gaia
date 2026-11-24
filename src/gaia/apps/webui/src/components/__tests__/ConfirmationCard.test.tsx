// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * `ConfirmationCard` — a `needs_confirmation` the chat cannot answer.
 *
 * The relay only sends this card for an agent too old to wait for an answer
 * (one that can wait gets the permission prompt instead), so the run has
 * already ended: the card must say the step did not run and how to get the
 * choice back, never offer an approval it cannot give.
 *
 *   - Names the step by its translated tool label, derived from `action` alone.
 *   - `summary` renders as a plain text node only: it can carry outside content.
 *   - No Approve/Allow button; Dismiss is local component state only.
 */

import { afterEach, describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmationCard } from '../render/ConfirmationCard';
import { useChatStore } from '../../stores/chatStore';
import { stripIsolates } from '../../i18n/registry';

const basicData = {
    action: 'write_file',
    summary: 'Write notes.txt in /home/you/projects/gaia',
};

afterEach(() => useChatStore.setState({ locale: 'en' }));

describe('ConfirmationCard — what it says', () => {
    it('renders the literal summary text', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText(basicData.summary)).toBeInTheDocument();
    });

    it('names the step by its friendly label, not the raw tool id', () => {
        const { container } = render(<ConfirmationCard data={basicData} />);

        expect(container.querySelector('.render-confirmation__label')?.textContent).toBe('Writing file');
        expect(container.textContent).not.toContain('write_file');
    });

    it('says the step did not run and that updating GAIA brings the choice back', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText('Not run')).toBeInTheDocument();
        expect(screen.getByText(/too old to ask for it here, so nothing ran\. Update GAIA to the latest version/)).toBeInTheDocument();
        expect(screen.queryByText(/approval needed|follow the assistant/i)).not.toBeInTheDocument();
    });

    it('speaks Arabic when the interface is in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        const { container } = render(<ConfirmationCard data={basicData} />);
        const shown = stripIsolates(container.textContent ?? '');

        for (const phrase of ['لم تُنفَّذ', 'كتابة ملف', 'حدّث GAIA إلى أحدث إصدار', 'إغلاق']) expect(shown).toContain(phrase);
        expect(screen.getByRole('button', { name: /إغلاق/ })).toBeInTheDocument();
    });

    it('wraps output in a .render-confirmation root, consistent with the render/ card family', () => {
        const { container } = render(<ConfirmationCard data={basicData} />);

        expect(container.querySelector('.render-confirmation')).not.toBeNull();
    });
});

describe('ConfirmationCard — the label derives from action, not summary', () => {
    it('does not derive the label from summary content, even when summary names a different action', () => {
        const { container } = render(
            <ConfirmationCard
                data={{
                    action: 'write_file',
                    summary: 'Consider archive_all_threads before doing anything else.',
                }}
            />,
        );

        expect(container.querySelector('.render-confirmation__label')?.textContent).toBe('Writing file');
        expect(screen.queryByText(/archive\s+all\s+threads/i)).not.toBeInTheDocument();
    });
});

describe('ConfirmationCard — unknown-action safety', () => {
    const unknownData = {
        action: 'zzz_unregistered_tool_9000',
        summary: 'An action GAIA has never seen before.',
    };

    it('renders without throwing for an action absent from any label', () => {
        expect(() => render(<ConfirmationCard data={unknownData} />)).not.toThrow();
    });

    it('reads an unlabelled tool as its own words, with no underscores left over', () => {
        const { container } = render(<ConfirmationCard data={unknownData} />);

        expect(container.querySelector('.render-confirmation__label')?.textContent).toBe('Using zzz unregistered tool 9000');
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

describe('ConfirmationCard — nothing to approve', () => {
    it('renders no Approve/Confirm/Allow button — the run has already ended', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.queryByRole('button', { name: /approve|confirm|allow/i })).not.toBeInTheDocument();
    });
});

describe('ConfirmationCard — dismiss behavior (local state only)', () => {
    it('hides the card content when Dismiss is clicked, with no confirm-server round trip', () => {
        render(<ConfirmationCard data={basicData} />);

        expect(screen.getByText(basicData.summary)).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

        expect(screen.queryByText(basicData.summary)).not.toBeInTheDocument();
        expect(screen.getByText('Not run: Writing file')).toBeInTheDocument();
    });
});

describe('ConfirmationCard — invalid payload safety', () => {
    it('renders the invalid-payload fallback (matching other render/ cards) for a malformed payload', () => {
        render(<ConfirmationCard data={{ action: 42 }} />);

        expect(screen.getByText('Invalid needs_confirmation payload')).toBeInTheDocument();
    });
});
