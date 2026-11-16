// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act } from 'react';
import { createRoot } from 'test-renderer';

import { useAttachments, type Attachments } from './useAttachments';

jest.mock('expo-file-system', () => ({ File: class {} }));
jest.mock('@/data', () => ({
    useGaiaClient: () => ({}),
    requireClient: () => ({ uploadFile: () => new Promise(() => {}) }),
}));
jest.mock('@/i18n/useT', () => ({
    useT: () => (key: string) => key,
    describeError: () => 'failed',
}));

describe('useAttachments', () => {
    it('keeps one chip when the same file is picked twice from different cache paths', async () => {
        let attachments!: Attachments;
        function Probe() {
            attachments = useAttachments();
            return null;
        }
        const r = createRoot();
        await act(async () => r.render(<Probe />));
        const pick = (uri: string) => ({ uri, name: 'a.pdf', size: 10, modified: 5 });
        await act(async () => attachments.add([pick('file:///cache/1/a.pdf')]));
        await act(async () => attachments.add([pick('file:///cache/2/a.pdf')]));
        expect(attachments.items).toHaveLength(1);
    });
});
