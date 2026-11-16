// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, waitFor } from '@testing-library/react-native';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data/GaiaClientProvider';
import { ThemeProvider } from '@/ui';

import { FilePreview } from './FilePreview';

const mockDelete = jest.fn();
const mockWrite = jest.fn();
jest.mock('expo-file-system', () => ({
    Paths: { cache: 'file:///cache/' },
    Directory: class {
        create() {}
    },
    File: class {
        uri: string;
        exists = true;
        constructor(_folder: unknown, name: string) {
            this.uri = `file:///cache/pc-files/${name}`;
        }
        create() {}
        write(bytes: Uint8Array) {
            mockWrite(this.uri, bytes);
        }
        delete() {
            mockDelete(this.uri);
        }
    },
}));
jest.mock('expo-crypto', () => ({
    CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
    digestStringAsync: jest.fn(async () => 'abc123'),
}));
jest.mock('expo-sharing', () => ({ shareAsync: jest.fn(), isAvailableAsync: jest.fn(async () => true) }));
jest.mock('expo-image', () => ({ Image: () => null }));

describe('FilePreview', () => {
    beforeEach(() => {
        mockDelete.mockClear();
        mockWrite.mockClear();
    });

    it('caches the file under a hashed name, never its path, and deletes it when the preview closes', async () => {
        const client = createFakeClient({ fileBytes: { 'C:\\Users\\me\\secret plans.png': new Uint8Array([1, 2, 3]) } });
        const view = await render(
            <ThemeProvider>
                <GaiaClientProvider client={client}>
                    <FilePreview target={{ kind: 'path', path: 'C:\\Users\\me\\secret plans.png', sessionId: 's1' }} onClose={() => {}} />
                </GaiaClientProvider>
            </ThemeProvider>,
        );
        await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1));
        expect(mockWrite.mock.calls[0]![0]).toBe('file:///cache/pc-files/abc123.png');
        expect(mockDelete).not.toHaveBeenCalled();
        await view.unmount();
        expect(mockDelete).toHaveBeenCalledWith('file:///cache/pc-files/abc123.png');
    });
});
