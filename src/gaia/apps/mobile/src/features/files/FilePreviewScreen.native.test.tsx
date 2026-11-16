// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { translator } from '@webui/i18n/translator';

import type { FilePreviewResponse } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data/GaiaClientProvider';
import { ThemeProvider } from '@/ui';

import { FilePreviewScreen } from './FilePreviewScreen';
import { Platform } from 'react-native';

jest.mock('expo-file-system', () => ({
    Paths: { cache: 'file:///cache/' },
    Directory: class {
        create() {}
    },
    File: class {
        uri = 'file:///cache/x';
        exists = true;
        create() {}
        write() {}
        delete() {}
    },
}));
jest.mock('expo-crypto', () => ({ CryptoDigestAlgorithm: { SHA256: 'SHA-256' }, digestStringAsync: jest.fn(async () => 'abc') }));
jest.mock('expo-sharing', () => ({ shareAsync: jest.fn() }));
const t = translator('en');

jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));
jest.mock('expo-image', () => ({ Image: () => null }));

const base = { extension: '.csv', modified: '', size: 10, size_display: '10 B', is_text: true, path: '/p', name: 'p' };
const previews: Record<string, FilePreviewResponse> = {
    '/table.csv': { ...base, preview_lines: ['a,b', '1,2'], total_lines: 3, columns: ['a', 'b'], row_count: 2 } as FilePreviewResponse,
    '/q3.pdf': { ...base, extension: '.pdf', is_text: false, preview_lines: [] } as FilePreviewResponse,
};

function mount(path: string, client = createFakeClient({ filePreviews: previews })) {
    return render(
        <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
            <ThemeProvider>
                <GaiaClientProvider client={client}>
                    <FilePreviewScreen target={{ kind: 'path', path, sessionId: 's1' }} onClose={() => {}} />
                </GaiaClientProvider>
            </ThemeProvider>
        </SafeAreaProvider>,
    );
}

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('FilePreviewScreen', () => {
    it('shows a table with its counts', async () => {
        await mount('/table.csv');
        expect(await screen.findByText(t('fileBrowser.columnsLabel'))).toBeTruthy();
        expect(screen.getByText('3 lines')).toBeTruthy();
        expect(screen.getByText('2 columns')).toBeTruthy();
        expect(screen.getAllByText('2 rows').length).toBeGreaterThan(0);
    });

    it('says a PDF has no preview instead of showing bytes', async () => {
        await mount('/q3.pdf');
        expect(await screen.findByText(t('fileBrowser.binary'))).toBeTruthy();
    });
});
