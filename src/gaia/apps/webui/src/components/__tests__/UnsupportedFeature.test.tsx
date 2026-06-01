// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
    getUnsupportedCategory,
    isExtensionSupported,
    UnsupportedFeatureBanner,
    UploadErrorToast,
} from '../UnsupportedFeature';

// ── Pure utility tests ─────────────────────────────────────────────────

describe('getUnsupportedCategory', () => {
    it('returns "Image" for .png', () => {
        const cat = getUnsupportedCategory('.png');
        expect(cat).not.toBeNull();
        expect(cat!.label).toBe('Image');
    });

    it('is case-insensitive', () => {
        expect(getUnsupportedCategory('.JPG')?.label).toBe('Image');
    });

    it('works without leading dot', () => {
        expect(getUnsupportedCategory('mp4')?.label).toBe('Video');
    });

    it('returns null for a supported extension', () => {
        expect(getUnsupportedCategory('.pdf')).toBeNull();
    });

    it('returns "Microsoft Office" for .docx', () => {
        expect(getUnsupportedCategory('.docx')?.label).toBe('Microsoft Office');
    });
});

describe('isExtensionSupported', () => {
    it('returns true for .pdf', () => {
        expect(isExtensionSupported('.pdf')).toBe(true);
    });

    it('returns true for .py', () => {
        expect(isExtensionSupported('.py')).toBe(true);
    });

    it('returns false for .exe', () => {
        expect(isExtensionSupported('.exe')).toBe(false);
    });

    it('is case-insensitive', () => {
        expect(isExtensionSupported('.PDF')).toBe(true);
    });
});

// ── Component render tests ─────────────────────────────────────────────

describe('UnsupportedFeatureBanner', () => {
    it('renders title and description', () => {
        render(
            <UnsupportedFeatureBanner
                title="Image files not supported"
                description="Cannot index image files for text search."
            />
        );

        expect(screen.getByText('Image files not supported')).toBeInTheDocument();
        expect(screen.getByText('Cannot index image files for text search.')).toBeInTheDocument();
    });

    it('renders alternatives when provided', () => {
        render(
            <UnsupportedFeatureBanner
                title="Video files not supported"
                description="Video files cannot be indexed."
                alternatives={['Extract subtitles first', 'Use gaia talk']}
            />
        );

        expect(screen.getByText('Extract subtitles first')).toBeInTheDocument();
        expect(screen.getByText('Use gaia talk')).toBeInTheDocument();
    });

    it('renders GitHub feature request link', () => {
        render(
            <UnsupportedFeatureBanner
                title="Test"
                description="Test description"
                featureTitle="Support XYZ"
            />
        );

        const link = screen.getByRole('link', { name: /request it on github/i });
        expect(link).toHaveAttribute('href', expect.stringContaining('github.com/amd/gaia'));
        expect(link).toHaveAttribute('target', '_blank');
    });
});

describe('UploadErrorToast', () => {
    it('renders file type error with dismiss button', async () => {
        const user = userEvent.setup();
        const onDismiss = vi.fn();

        render(
            <UploadErrorToast
                filename="photo.png"
                error="Unsupported file type"
                onDismiss={onDismiss}
                timeout={0}
            />
        );

        expect(screen.getByText('Image files not supported')).toBeInTheDocument();
        expect(screen.getByRole('link', { name: /request this feature/i })).toBeInTheDocument();

        await user.click(screen.getByRole('button', { name: /dismiss/i }));
        expect(onDismiss).toHaveBeenCalledOnce();
    });

    it('renders connection error correctly', () => {
        render(
            <UploadErrorToast
                filename="doc.pdf"
                error="Failed to fetch"
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText('Connection error')).toBeInTheDocument();
    });

    it('renders generic error with bug report link', () => {
        render(
            <UploadErrorToast
                filename="doc.pdf"
                error="Internal server error"
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText(/failed to index/i)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: /report this issue/i })).toBeInTheDocument();
    });
});
