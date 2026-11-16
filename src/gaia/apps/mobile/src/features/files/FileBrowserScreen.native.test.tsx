// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { translator } from '@webui/i18n/translator';

import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { fileBrowserScene, FILE_BROWSER_START } from './devScenes';
import { FileBrowserScreen } from './FileBrowserScreen';
import { Platform } from 'react-native';

const t = translator('en');

jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));
jest.mock('expo-image', () => ({ Image: () => null }));

function mount(onAsk: (prompt: string) => boolean) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const client = fileBrowserScene('folder');
    const wrap = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>{children}</ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
    return render(<FileBrowserScreen chatId="chat-1" onClose={() => {}} onAsk={onAsk} initial={FILE_BROWSER_START.folder} />, { wrapper: wrap });
}

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('FileBrowserScreen selection bar', () => {
    it('stays disabled until a file is picked, then asks GAIA with the localized prompt naming the file', async () => {
        const onAsk = jest.fn(() => true);
        await mount(onAsk);
        const ask = await screen.findByLabelText(t('fileBrowser.askGaia'));
        const library = screen.getByLabelText(t('fileBrowser.addToLibrary'));
        expect(ask.props.accessibilityState?.disabled).toBe(true);
        expect(library.props.accessibilityState?.disabled).toBe(true);

        fireEvent.press(await screen.findByLabelText(t('fileBrowser.selectAria', { name: 'q3-board.pdf' })));
        await waitFor(() => expect(screen.getByLabelText(t('fileBrowser.askGaia')).props.accessibilityState?.disabled).toBeFalsy());
        fireEvent.press(screen.getByLabelText(t('fileBrowser.askGaia')));

        await waitFor(() => expect(onAsk).toHaveBeenCalledWith(t('mobileApp.files.askOne', { name: 'q3-board.pdf' })));
    });
});
