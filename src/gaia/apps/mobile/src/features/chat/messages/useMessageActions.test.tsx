// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { createFakeClient } from '@/api/fake';
import type { Message } from '@/api/client.types';
import { GaiaClientProvider } from '@/data';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';
import { sheetStore } from '@/ui/overlays/sheetStore';

import { MessageActionsContext } from './MessageActionsContext';
import { UserBubble } from './UserBubble';

// The sheet store is module-wide: a sheet still closing from one test would hide the next one's screen.
afterEach(() => act(() => sheetStore.reset()));

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const t = translator('en');
const question: Message = {
    id: 7,
    session_id: 's',
    role: 'user',
    content: 'What did the job do?',
    created_at: '2026-11-13T14:05:09Z',
    rag_sources: null,
};

async function setup(laterTurns: number, readOnly = false) {
    const client = createFakeClient({ messages: { s: [question] } });
    const send = (text: string) => {
        void client.sendMessage('s', text)[Symbol.asyncIterator]().next();
        return true;
    };
    await render(
        <QueryClientProvider client={new QueryClient()}>
            <GaiaClientProvider client={client}>
                <ThemeProvider>
                    <SheetHost>
                        <MessageActionsContext.Provider value={{ send, busy: false, readOnly }}>
                            <UserBubble message={question} context={{ laterTurns }} />
                        </MessageActionsContext.Provider>
                    </SheetHost>
                </ThemeProvider>
            </GaiaClientProvider>
        </QueryClientProvider>,
    );
    return client;
}

describe('message actions', () => {
    // The sheet store is module-wide: a sheet still closing from the last test would hide this one's messages.
    afterEach(() => act(() => sheetStore.reset()));

    it('Delete asks first, then deletes only that message', async () => {
        const client = await setup(0);
        await fireEvent(screen.getByText(question.content), 'longPress');
        await fireEvent.press(screen.getByLabelText(t('msg.delete')));
        expect(await screen.findByText(t('msg.confirmDeleteAria'))).toBeTruthy();
        expect(client.deletedOne).toEqual([]);
        await fireEvent.press(screen.getByLabelText(t('msg.delete')));
        await waitFor(() => expect(client.deletedOne).toEqual([{ sessionId: 's', messageId: 7 }]));
        expect(client.deleted).toEqual([]);
    });

    it("Edit's Send deletes from the message down, then sends the edited text", async () => {
        const client = await setup(0);
        await fireEvent(screen.getByText(question.content), 'longPress');
        await fireEvent.press(screen.getByLabelText(t('msg.edit')));
        await fireEvent.changeText(await screen.findByLabelText(t('msg.editLabel')), 'What did the job copy?');
        await fireEvent.press(screen.getByLabelText(t('msg.editSend')));
        await waitFor(() => expect(client.sent).toEqual([{ sessionId: 's', message: 'What did the job copy?' }]));
        expect(client.deleted).toEqual([{ sessionId: 's', messageId: 7 }]);
        await act(async () => {});
    });

    it('a retired agent\'s chat offers no Edit or Resend, but keeps Copy and Delete', async () => {
        await setup(0, true);
        await fireEvent(screen.getByText(question.content), 'longPress');
        expect(await screen.findByLabelText(t('msg.copy'))).toBeTruthy();
        expect(screen.getByLabelText(t('msg.delete'))).toBeTruthy();
        expect(screen.queryByLabelText(t('msg.resend'))).toBeNull();
        expect(screen.queryByLabelText(t('msg.edit'))).toBeNull();
    });
});
