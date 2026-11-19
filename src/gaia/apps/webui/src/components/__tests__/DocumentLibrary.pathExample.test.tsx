// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import * as api from '../../services/api';

vi.mock('../../services/api');

afterEach(() => vi.restoreAllMocks());

it('gives a Mac user a Mac path example, not a Windows one', async () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total: 0 } as never);
    render(<DocumentLibrary />);

    const box = await screen.findByLabelText('File path to add');
    expect(box.getAttribute('placeholder')).toBe('/Users/you/Documents/manual.pdf');
});

it('keeps the sentence out of the path box so a right-to-left sentence cannot scramble the example', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total: 0 } as never);
    render(<DocumentLibrary />);

    const box = await screen.findByLabelText('File path to add');
    expect(box.getAttribute('placeholder')).not.toMatch(/enter a file or folder path/i);
    expect(screen.getByText('Or enter a file or folder path')).toBeInTheDocument();
});
