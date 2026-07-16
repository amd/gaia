// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the render->component card registry (issue #2108,
 * Increment 1). The modules under test do not exist yet:
 *
 *   - src/components/render/RenderCard.tsx
 *   - src/components/render/UnsupportedCard.tsx
 *   - src/components/render/CardErrorBoundary.tsx
 *
 * These tests are EXPECTED to fail right now — that is correct TDD. They
 * pin the frozen API surface the implementer must conform to; do not treat
 * a future failure here as license to change the assertions to match a
 * different implementation shape without confirming it against the plan.
 *
 * Only `email_pre_scan` is registered as of this increment. Do not add
 * assertions for `table` / `key_value` / `list` / `image` / `diff` (present
 * or absent) — those land in Increment 2.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RenderCard } from '../render/RenderCard';
import { CardErrorBoundary } from '../render/CardErrorBoundary';
import { UnsupportedCard } from '../render/UnsupportedCard';

const preScanPayload = {
    kind: 'email_pre_scan',
    urgent: [{ message_id: 'm1', sender: 'Alice <alice@example.com>', subject: 'Server down' }],
    actionable: [],
    informational_count: 2,
    suggested_archives: [],
    suggested_drafts: [],
};

describe('UnsupportedCard module', () => {
    // Deliberately minimal: the plan names this as its own module but does
    // not freeze its prop shape (unlike RenderCard/CardErrorBoundary below),
    // so this only pins "the module and export exist" — its rendered output
    // is pinned via RenderCard, which is its only specified public surface.
    it('is exported from render/UnsupportedCard', () => {
        expect(UnsupportedCard).toBeDefined();
    });
});

describe('RenderCard — unregistered render key', () => {
    it('wraps output in a .render-card div', () => {
        const { container } = render(
            <RenderCard render="nonexistent_widget" data={{ marker: 'unregistered-payload-marker' }} />,
        );

        expect(container.querySelector('.render-card')).not.toBeNull();
    });

    it('renders the exact "Unsupported card type" fallback text for an unregistered key', () => {
        // A deliberately nonsense render key — not one of the real card
        // types (table/list/etc.) landing in Increment 2, so this stays
        // valid across increments.
        render(<RenderCard render="nonexistent_widget" data={{ marker: 'unregistered-payload-marker' }} />);

        expect(screen.getByText('Unsupported card type: "nonexistent_widget"')).toBeInTheDocument();
    });

    it('renders a <details> element with a JSON dump of the unregistered payload', () => {
        const { container } = render(
            <RenderCard render="nonexistent_widget" data={{ marker: 'unregistered-payload-marker' }} />,
        );

        const details = container.querySelector('details');
        expect(details).not.toBeNull();
        expect(details?.textContent).toContain('unregistered-payload-marker');
    });
});

describe('RenderCard — email_pre_scan registry entry', () => {
    it('renders the real EmailPreScanCard for the bare/target payload shape', () => {
        render(<RenderCard render="email_pre_scan" data={preScanPayload} />);

        expect(screen.getByText('Server down')).toBeInTheDocument();
        expect(screen.getByRole('region', { name: 'Inbox pre-scan' })).toBeInTheDocument();
    });

    it('renders identically for the { ok, data } envelope shape', () => {
        render(<RenderCard render="email_pre_scan" data={{ ok: true, data: preScanPayload }} />);

        expect(screen.getByText('Server down')).toBeInTheDocument();
        expect(screen.getByRole('region', { name: 'Inbox pre-scan' })).toBeInTheDocument();
    });

    it('renders the invalid-payload fallback (with JSON dump) for a payload matching neither shape', () => {
        const { container } = render(<RenderCard render="email_pre_scan" data={{ foo: 'bar' }} />);

        expect(screen.getByText('Invalid email_pre_scan payload')).toBeInTheDocument();

        const details = container.querySelector('details');
        expect(details).not.toBeNull();
        expect(details?.textContent).toContain('bar');
    });
});

describe('CardErrorBoundary', () => {
    it('renders children normally when nothing throws', () => {
        render(
            <CardErrorBoundary>
                <div>safe content</div>
            </CardErrorBoundary>,
        );

        expect(screen.getByText('safe content')).toBeInTheDocument();
    });

    it('catches a throwing child and renders the fallback alert instead of propagating', () => {
        function ThrowingComponent(): never {
            throw new Error('boom');
        }

        // React logs caught render errors to console.error; mock it so the
        // expected failure doesn't pollute test output, and so we can assert
        // the boundary actually caught something.
        const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

        render(
            <CardErrorBoundary>
                <ThrowingComponent />
            </CardErrorBoundary>,
        );

        expect(screen.getByRole('alert')).toBeInTheDocument();
        expect(screen.getByText('Card failed to render')).toBeInTheDocument();
        expect(consoleErrorSpy).toHaveBeenCalled();

        consoleErrorSpy.mockRestore();
    });
});
