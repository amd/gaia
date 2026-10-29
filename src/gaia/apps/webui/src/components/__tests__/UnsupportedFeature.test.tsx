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

    it('returns null for .docx — Word indexing is supported', () => {
        expect(getUnsupportedCategory('.docx')).toBeNull();
    });

    it('returns null for .pptx — PowerPoint indexing is supported', () => {
        expect(getUnsupportedCategory('.pptx')).toBeNull();
    });

    it('keeps legacy Office formats (.ppt, .doc, .xls) blocked', () => {
        expect(getUnsupportedCategory('.ppt')?.label).toBe('Microsoft Office');
        expect(getUnsupportedCategory('.doc')?.label).toBe('Microsoft Office');
        expect(getUnsupportedCategory('.xls')?.label).toBe('Microsoft Office');
    });
});

describe('isExtensionSupported', () => {
    it('returns true for .pdf', () => {
        expect(isExtensionSupported('.pdf')).toBe(true);
    });

    it('returns true for .py', () => {
        expect(isExtensionSupported('.py')).toBe(true);
    });

    it('returns true for .pptx — modern PowerPoint is indexable', () => {
        expect(isExtensionSupported('.pptx')).toBe(true);
    });

    it('returns true for .docx — modern Word is indexable', () => {
        expect(isExtensionSupported('.docx')).toBe(true);
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

    it('says a missing file does not exist, with no bug report link', () => {
        render(
            <UploadErrorToast
                filename="missing.pdf"
                error="File not found: there is no file at C:\Users\me\missing.pdf. Check the path for typos."
                status={404}
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText(`"missing.pdf" doesn't exist`)).toBeInTheDocument();
        expect(screen.getByText(/there is no file at .*Check the path/)).toBeInTheDocument();
        expect(screen.queryByRole('link')).toBeNull();
    });

    it('explains the readable folders without claiming the file exists', () => {
        render(
            <UploadErrorToast
                filename="missing.pdf"
                error="GAIA can only read documents under C:\Users\me, and C:\nope\missing.pdf is outside them. Check the path is right."
                status={403}
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText("GAIA can't read files in that location")).toBeInTheDocument();
        expect(screen.getByText(/can only read documents under.*drop the file here instead \(up to 20 MB\)/)).toBeInTheDocument();
        expect(screen.queryByText(/Access denied/)).toBeNull();
        expect(screen.queryByRole('link')).toBeNull();
    });

    it('names a file with no extension plainly', () => {
        render(
            <UploadErrorToast
                filename="README"
                error="File type not allowed: 'README' has no file extension, so GAIA can't tell what kind of document it is."
                status={400}
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText('"README" has no file extension')).toBeInTheDocument();
        expect(screen.queryByText(/File type not supported: $/)).toBeNull();
        expect(screen.queryByRole('link')).toBeNull();
    });

    it('tells the user to re-save a legacy or password-protected workbook, with no bug report link', () => {
        render(
            <UploadErrorToast
                filename="labs.xlsx"
                error="Could not read labs.xlsx: it is a legacy .xls workbook or is protected with a password."
                status={422}
                kind="unsupported_workbook"
                onDismiss={() => {}}
                timeout={0}
            />
        );

        expect(screen.getByText(`"labs.xlsx" can't be read`)).toBeInTheDocument();
        expect(screen.getByText(/save a copy as \.xlsx without a password/)).toBeInTheDocument();
        expect(screen.queryByRole('link')).toBeNull();
    });
});
