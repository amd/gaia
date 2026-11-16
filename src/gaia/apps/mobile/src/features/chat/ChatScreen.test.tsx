// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { item, type QueuedMessage } from '@webui/utils/messageQueue';

import type { ConnectorListEntry } from '@webui/types';

import type { Message, StreamEvent } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { useComposerQueueStore } from '@/features/composer/composerQueue';
import { translator } from '@/i18n/useT';
import { useChatStreamStore } from '@/state/chatStreams';
import { ThemeProvider } from '@/ui';

import { ChatScreen } from './ChatScreen';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('./header', () => ({ ChatHeader: () => null }));

const t = translator('en');
const CHAT = 'chat-1';

function message(id: number, role: 'user' | 'assistant', content: string): Message {
    return { id, session_id: CHAT, role, content, created_at: '2026-11-14T00:00:00Z', rag_sources: null };
}

const SESSION = {
    id: CHAT,
    title: 'A chat',
    created_at: '2026-11-14T00:00:00Z',
    updated_at: '2026-11-14T00:00:00Z',
    model: '',
    system_prompt: null,
    message_count: 1,
    document_ids: [],
};

function gate() {
    let open!: () => void;
    const opened = new Promise<void>((resolve) => (open = resolve));
    return { opened, open };
}

async function mount(client: ReturnType<typeof createFakeClient>) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>{children}</ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
    await render(<ChatScreen sessionId={CHAT} />, { wrapper: wrap });
}

async function sendText(text: string) {
    await fireEvent.changeText(await screen.findByTestId('composer-input'), text);
    await fireEvent.press(screen.getByTestId('composer-send'));
}

describe('ChatScreen', () => {
    beforeEach(() => {
        useChatStreamStore.setState({ turns: {}, resolved: {} });
        useComposerQueueStore.setState({ byChat: {}, drafts: {} });
    });

    it('streams an answer, shows the stopped note after Stop, and Continue sends the web UI text', async () => {
        const stopped = gate();
        const messages = { [CHAT]: [message(1, 'user', 'Tell a story')] };
        const client = createFakeClient({
            sessions: [SESSION],
            messages,
            replies: async function* (): AsyncGenerator<StreamEvent> {
                yield { type: 'chunk', content: 'Once upon' };
                await stopped.opened;
                yield { type: 'done', cancelled: true, message_id: 2, content: 'Once upon' };
            },
        });
        await mount(client);

        await sendText('Tell a story');
        expect(await screen.findByText(/Once upon/)).toBeTruthy();

        await fireEvent.press(screen.getByTestId('composer-stop'));
        expect(client.cancelled).toEqual([CHAT]);
        messages[CHAT]!.push(message(2, 'assistant', 'Once upon'));
        await act(async () => stopped.open());

        expect(await screen.findByText(t('msg.stopped'))).toBeTruthy();
        await fireEvent.press(screen.getByTestId('continue'));
        await waitFor(() => expect(client.sent.map((s) => s.message)).toEqual(['Tell a story', t('msg.continuePrompt')]));
    });

    it('shows a failed turn as its classified text and Try again deletes the exchange and resends', async () => {
        const messages = { [CHAT]: [message(1, 'user', 'Summarize it')] };
        let attempt = 0;
        const client = createFakeClient({
            sessions: [SESSION],
            messages,
            replies: async function* (): AsyncGenerator<StreamEvent> {
                attempt += 1;
                if (attempt === 1) yield { type: 'error', content: 'Lemonade could not get response' };
                else yield { type: 'done', message_id: 3, content: 'Done.' };
            },
        });
        await mount(client);

        await sendText('Summarize it');
        expect(await screen.findByText(t('chatError.modelServer'))).toBeTruthy();

        await fireEvent.press(await screen.findByTestId('try-again'));
        await waitFor(() => expect(client.sent.map((s) => s.message)).toEqual(['Summarize it', 'Summarize it']));
        expect(client.deleted).toEqual([{ sessionId: CHAT, messageId: 1 }]);
    });

    it('shows the model-server-down notice under a failed reply', async () => {
        const client = createFakeClient({
            sessions: [SESSION],
            messages: { [CHAT]: [message(1, 'user', 'Hi')] },
            replies: async function* (): AsyncGenerator<StreamEvent> {
                yield { type: 'done', message_id: 2, content: 'Lemonade is not running.', failed: true, failure: 'model_server_down' };
            },
        });
        await mount(client);

        await sendText('Hi');
        expect(await screen.findByTestId('server-down-notice')).toBeTruthy();
        expect(screen.queryByTestId('error-banner')).toBeNull();
    });

    it('shows the model-server-down fix as the saved failed reply\'s only failure surface', async () => {
        const messages = { [CHAT]: [message(1, 'user', 'Hi')] };
        const client = createFakeClient({
            sessions: [SESSION],
            messages,
            replies: async function* (): AsyncGenerator<StreamEvent> {
                messages[CHAT].push({ ...message(2, 'assistant', 'Lemonade is not running.'), failed: true });
                yield { type: 'done', message_id: 2, content: 'Lemonade is not running.', failed: true, failure: 'model_server_down' };
            },
        });
        await mount(client);

        await sendText('Hi');
        expect(await screen.findByTestId('server-down-notice')).toBeTruthy();
        expect(await screen.findByText('Lemonade is not running.')).toBeTruthy();
        expect(screen.queryByTestId('error-banner')).toBeNull();
    });

    it('Stop returns the queued messages to the box and sends none of them', async () => {
        const finish = gate();
        const client = createFakeClient({
            sessions: [SESSION],
            messages: { [CHAT]: [message(1, 'user', 'first')] },
            replies: async function* (): AsyncGenerator<StreamEvent> {
                yield { type: 'chunk', content: 'Working' };
                await finish.opened;
                yield { type: 'done', cancelled: true, message_id: 2, content: 'Working' };
            },
        });
        await mount(client);

        await sendText('first');
        await screen.findByText(/Working/);
        await sendText('second');
        await sendText('third');
        await fireEvent.press(screen.getByTestId('composer-stop'));
        await act(async () => finish.open());

        await waitFor(() => expect(screen.getByTestId('composer-input').props.value).toBe('second\n\nthird'));
        expect(client.sent.map((s) => s.message)).toEqual(['first']);
        expect(useComposerQueueStore.getState().byChat[CHAT]).toEqual([]);
    });

    it('holds the messages behind a failed one until Send again', async () => {
        const failed: QueuedMessage = { ...item('first', 'failed'), error: 'no connection' };
        useComposerQueueStore.setState({ byChat: { [CHAT]: [failed, item('second', 'queued')] } });
        const client = createFakeClient({
            sessions: [SESSION],
            messages: { [CHAT]: [] },
            replies: [{ type: 'done', message_id: 2, content: 'ok' }],
        });
        await mount(client);

        expect(await screen.findByText(t('queue.notSentWhy', { error: 'no connection' }))).toBeTruthy();
        expect(client.sent).toEqual([]);

        await fireEvent.press(screen.getByTestId(`queued-retry-${failed.id}`));
        await waitFor(() => expect(client.sent.map((s) => s.message)).toEqual(['first', 'second']));
    });

    it('offers Ask again for a Connect card once its account is connected, and tells the person to connect on the PC', async () => {
        const card = { render: 'connect_account', data: { kind: 'connect_account', providers: ['google'] } };
        const reply = { ...message(2, 'assistant', 'I need access to your mailbox first.'), cards: [card] };
        const connected = { id: 'google', display_name: 'Google', configured: true } as ConnectorListEntry;
        await mount(createFakeClient({ sessions: [SESSION], messages: { [CHAT]: [message(1, 'user', 'Check my mail'), reply] }, connectors: [connected] }));

        expect(await screen.findByTestId('card-connect-account')).toBeTruthy();
        expect(await screen.findByText(t('mobileApp.connectorRetry.askAgain'))).toBeTruthy();
    });

    it('sends a typed answer to the waiting question as its value, shown as typed', async () => {
        const hold = gate();
        const client = createFakeClient({
            sessions: [SESSION],
            messages: { [CHAT]: [message(1, 'user', 'Pick one')] },
            replies: async function* (): AsyncGenerator<StreamEvent> {
                yield {
                    type: 'needs_input',
                    request_id: 'q1',
                    question: 'Which color?',
                    options: [
                        { value: 'r', label: 'Red', description: '' },
                        { value: 'b', label: 'Blue', description: '' },
                    ],
                    allow_free_text: false,
                };
                await hold.opened;
                yield { type: 'done', message_id: 2, content: '' };
            },
        });
        await mount(client);

        await sendText('Pick one');
        await screen.findByLabelText(t('composer.sendAnswer'));
        await sendText('blue');
        await waitFor(() => expect(client.answers).toEqual([{ session_id: CHAT, request_id: 'q1', value: 'b', display: 'blue' }]));
        expect(client.sent.map((m) => m.message)).toEqual(['Pick one']);
        await act(async () => hold.open());
    });

    describe('tool approval', () => {
        const REQUEST: StreamEvent = { type: 'permission_request', tool: 'write_file', confirm_id: 'c1', always_scope: 'write_file', timeout_seconds: 60 };
        let hold = gate();
        beforeEach(() => {
            hold = gate();
        });
        // The stream stays open on the request, as a turn waiting on the person does.
        const permissionClient = (event: StreamEvent) =>
            createFakeClient({
                sessions: [SESSION],
                messages: { [CHAT]: [message(1, 'user', 'Save it')] },
                replies: async function* (): AsyncGenerator<StreamEvent> {
                    yield event;
                    await hold.opened;
                    yield { type: 'done', message_id: 2, content: '' };
                },
            });

        it('shows the waiting prompt, posts the answer, and clears it as answered', async () => {
            const client = permissionClient(REQUEST);
            await mount(client);
            await sendText('Save it');
            await fireEvent.press(await screen.findByTestId('perm-allow'));
            await waitFor(() => expect(client.confirmations).toEqual([{ session_id: CHAT, approved: true, always: false, confirm_id: 'c1' }]));
            await waitFor(() => expect(screen.queryByTestId('perm-card')).toBeNull());
            expect(useChatStreamStore.getState().resolved[CHAT]?.c1).toEqual({ state: 'answered' });
            await act(async () => hold.open());
        });

        it('clears a prompt whose deadline has passed as expired', async () => {
            await mount(permissionClient({ ...REQUEST, expires_at: Date.now() / 1000 - 5 }));
            await sendText('Save it');
            await waitFor(() => expect(useChatStreamStore.getState().resolved[CHAT]?.c1).toEqual({ state: 'expired' }));
            expect(screen.queryByTestId('perm-card')).toBeNull();
            expect(useChatStreamStore.getState().turns[CHAT]?.pendingPermission).toBeNull();
        });
    });
});
