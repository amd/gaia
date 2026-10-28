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

    const box = await screen.findByLabelText('File path to index');
    expect(box.getAttribute('placeholder')).toContain('/Users/you/Documents/manual.pdf');
    expect(box.getAttribute('placeholder')).not.toContain('C:\\');
});
