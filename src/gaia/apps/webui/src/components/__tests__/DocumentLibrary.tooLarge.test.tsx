// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';

vi.mock('../../services/api');
vi.mock('../../stores/modelNeededStore', () => ({ ensureModel: vi.fn(async () => true) }));

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ currentSessionId: null, sessions: [], showDocLibrary: true, draftDocumentIds: [], documents: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
});

describe('a file over the library size limit', () => {
    it('names the limit and offers no bug report', async () => {
        mockedApi.uploadDocumentBlob.mockRejectedValue(Object.assign(new Error('File too large. Maximum size is 20 MB.'), {
            httpStatus: 413,
            reason: 'too_large',
            body: { status: 'too_large', max_mb: '20' },
        }));
        render(<DocumentLibrary />);

        const input = document.querySelector('.modal-overlay input[type="file"]') as HTMLInputElement;
        fireEvent.change(input, { target: { files: [new File(['x'], 'big.txt', { type: 'text/plain' })] } });

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('"big.txt" is too large');
        expect(alert).toHaveTextContent('The library takes files up to 20 MB.');
        expect(alert).not.toHaveTextContent(/Failed to index/);
        expect(screen.queryByRole('link', { name: /report this issue/i })).toBeNull();
    });
});
