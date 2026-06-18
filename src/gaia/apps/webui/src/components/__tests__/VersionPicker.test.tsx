// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { VersionPicker } from '../VersionPicker';
import type { ReleaseInfo } from '../../hooks/useUpdateStatus';

// ── Fixtures ─────────────────────────────────────────────────────────────────

const releases: ReleaseInfo[] = [
    {
        version: '0.21.0',
        tag: 'v0.21.0',
        date: '2026-06-01T00:00:00Z',
        notesUrl: 'https://github.com/amd/gaia/releases/tag/v0.21.0',
        isCurrent: true,
        isPinned: false,
    },
    {
        version: '0.20.0',
        tag: 'v0.20.0',
        date: '2026-05-01T00:00:00Z',
        notesUrl: 'https://github.com/amd/gaia/releases/tag/v0.20.0',
        isCurrent: false,
        isPinned: false,
    },
    {
        version: '0.19.0',
        tag: 'v0.19.0',
        date: '2026-04-01T00:00:00Z',
        notesUrl: 'https://github.com/amd/gaia/releases/tag/v0.19.0',
        isCurrent: false,
        isPinned: true,
    },
];

function makeUpdaterBridge(overrides: {
    listReleases?: () => Promise<ReleaseInfo[] | { error: string }>;
    installVersion?: (tag: string) => Promise<void>;
    resumeUpdates?: () => Promise<unknown>;
} = {}) {
    return {
        getStatus: vi.fn(async () => ({ status: 'idle', version: null, progress: 0, releaseNotes: null, error: null, currentVersion: '0.21.0', pinnedVersion: null })),
        check: vi.fn(async () => ({})),
        onStatusChange: vi.fn(() => () => {}),
        listReleases: vi.fn(async () => releases),
        installVersion: vi.fn(async (_tag: string) => {}),
        resumeUpdates: vi.fn(async () => ({})),
        ...overrides,
    };
}

beforeEach(() => {
    vi.stubGlobal('gaiaUpdater', makeUpdaterBridge());
});

afterEach(() => {
    vi.unstubAllGlobals();
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe('VersionPicker', () => {
    it('renders a dialog with role="dialog"', async () => {
        render(<VersionPicker onClose={() => {}} />);
        expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('shows a loading state initially', () => {
        render(<VersionPicker onClose={() => {}} />);
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        // Loading spinner or text present immediately
        expect(document.querySelector('[aria-busy="true"]') || screen.queryByText(/loading/i)).toBeTruthy();
    });

    it('renders the list of releases', async () => {
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => {
            expect(screen.getByText('0.21.0')).toBeInTheDocument();
        });

        expect(screen.getByText('0.20.0')).toBeInTheDocument();
        expect(screen.getByText('0.19.0')).toBeInTheDocument();
    });

    it('marks the current release as "Installed"', async () => {
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => screen.getByText('0.21.0'));
        expect(screen.getByText(/installed/i)).toBeInTheDocument();
    });

    it('marks pinned release with pinned indicator', async () => {
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => screen.getByText('0.19.0'));
        expect(screen.getByText(/pinned/i)).toBeInTheDocument();
    });

    it('clicking an older release shows a confirm step mentioning the version and restart', async () => {
        const user = userEvent.setup();
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => screen.getByText('0.20.0'));

        // Find and click the 0.20.0 row button
        const row = screen.getByRole('button', { name: /0\.20\.0/i });
        await user.click(row);

        // Confirm step should mention version and restart
        expect(screen.getAllByText(/0\.20\.0/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/restart/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/downgrade/i).length).toBeGreaterThan(0);
    });

    it('does not steal focus from the focused control when the view changes', async () => {
        const user = userEvent.setup();
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => screen.getByText('0.20.0'));

        // Move to the confirm view, then focus the "Back" button explicitly.
        await user.click(screen.getByRole('button', { name: /0\.20\.0/i }));
        const backBtn = screen.getByRole('button', { name: /back/i });
        backBtn.focus();
        expect(document.activeElement).toBe(backBtn);

        // Transition back to the list view. The focus-trap effect must NOT
        // yank focus to the first focusable element on this transition.
        await user.click(backBtn);
        await waitFor(() => screen.getByText('0.20.0'));

        const closeBtn = screen.getByRole('button', { name: /close/i });
        expect(document.activeElement).not.toBe(closeBtn);
    });

    it('confirming the downgrade calls installVersion with the tag', async () => {
        const installMock = vi.fn(async () => {});
        vi.stubGlobal('gaiaUpdater', makeUpdaterBridge({ installVersion: installMock }));

        const user = userEvent.setup();
        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => screen.getByText('0.20.0'));

        await user.click(screen.getByRole('button', { name: /0\.20\.0/i }));

        // Find and click confirm button
        const confirmBtn = screen.getByRole('button', { name: /confirm|downgrade|install/i });
        await user.click(confirmBtn);

        expect(installMock).toHaveBeenCalledWith('v0.20.0');
    });

    it('renders actionable error message when listReleases fails', async () => {
        vi.stubGlobal('gaiaUpdater', makeUpdaterBridge({
            listReleases: async () => ({ error: "Couldn't reach GitHub to list releases — check your connection" }),
        }));

        render(<VersionPicker onClose={() => {}} />);

        await waitFor(() => {
            expect(screen.getByText(/couldn't reach github/i)).toBeInTheDocument();
        });

        // Should NOT render an empty list — must show the error
        expect(screen.queryByRole('button', { name: /0\.\d+\.\d+/i })).toBeNull();
    });

    it('calls onClose when Escape is pressed', async () => {
        const user = userEvent.setup();
        const onClose = vi.fn();
        render(<VersionPicker onClose={onClose} />);

        await user.keyboard('{Escape}');

        expect(onClose).toHaveBeenCalled();
    });

    it('calls onClose when close button is clicked', async () => {
        const user = userEvent.setup();
        const onClose = vi.fn();
        render(<VersionPicker onClose={onClose} />);

        const closeBtn = screen.getByRole('button', { name: /close/i });
        await user.click(closeBtn);

        expect(onClose).toHaveBeenCalled();
    });

    it('does not render a roll-back button for the current release', async () => {
        render(<VersionPicker onClose={() => {}} />);
        await waitFor(() => screen.getByText('0.21.0'));

        // The current version row should NOT be clickable as a downgrade target
        const currentRow = screen.getByText('0.21.0').closest('[data-version]');
        if (currentRow) {
            expect(currentRow.getAttribute('aria-disabled') === 'true' ||
                   !currentRow.matches('button')).toBe(true);
        }
    });
});
