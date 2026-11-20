// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import * as Clipboard from 'expo-clipboard';

import { USER_COLLAPSE_LINES } from '@webui/utils/longContent';
import type { Message } from '@/api/client.types';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { UserBubble } from './UserBubble';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const t = translator('en');
const content = Array.from({ length: USER_COLLAPSE_LINES + 5 }, (_, i) => `line ${i}`).join('\n');
const message: Message = {
    id: 1,
    session_id: 's',
    role: 'user',
    content,
    created_at: '2026-11-13T14:05:09Z',
    rag_sources: null,
};

describe('UserBubble', () => {
    it('collapses a long message, brings it back into view on "Show less", and copies from the long-press sheet', async () => {
        const onCollapse = jest.fn();
        await render(
            <QueryClientProvider client={new QueryClient()}>
                <ThemeProvider>
                    <SheetHost>
                        <UserBubble message={message} onCollapse={onCollapse} />
                    </SheetHost>
                </ThemeProvider>
            </QueryClientProvider>,
        );

        const showAll = screen.getByLabelText(/^Show all/);
        await fireEvent.press(showAll);
        expect(onCollapse).not.toHaveBeenCalled();
        await fireEvent.press(screen.getByLabelText(t('msg.showLess')));
        expect(onCollapse).toHaveBeenCalledTimes(1);

        await fireEvent(screen.getByText(/line 0/), 'longPress');
        expect(screen.getByText(/^Sent /)).toBeTruthy();
        await fireEvent.press(screen.getByLabelText(t('msg.copy')));
        expect(Clipboard.setStringAsync).toHaveBeenCalledWith(content);
    });
});
