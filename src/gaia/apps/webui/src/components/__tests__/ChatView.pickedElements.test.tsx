// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Elements picked in the browser pane ride the next message: in the request, and as chips in its bubble. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { usePickedElementsStore } from '../../stores/pickedElementsStore';
import type { Session } from '../../types';
import type { PickedElement } from '../../../services/browser-pane/contract';
import * as api from '../../services/api';
import saveButton from '../../../../../../../tests/fixtures/browser_pane/picked/save_button.json';
import wireSaveButton from '../../../../../../../tests/fixtures/browser_pane/picked/wire_save_button.json';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);
const SAVE = saveButton as PickedElement;

const SESSION: Session = {
    id: 'session-1',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

beforeEach(() => {
    vi.clearAllMocks();
    for (const fn of Object.values(mockedApi)) {
        if (vi.isMockFunction(fn)) fn.mockReturnValue(new Promise(() => {}) as never);
    }
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} } as never);
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.uploadFile.mockResolvedValue({
        filename: 'picked-p1.png', original_name: 'picked-p1.png', url: '/api/files/uploads/picked-p1.png',
        size: 3, content_type: 'image/png', is_image: true,
    });
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    usePickedElementsStore.setState({ chips: [], dismissed: [] });
    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
});

async function pickSaveButton() {
    act(() => usePickedElementsStore.getState().applyPick({ run: 1, elements: [{ ...SAVE, crop: btoa('png') }] }));
    act(() => usePickedElementsStore.getState().toggle('p1', 'screenshot'));
    await waitFor(() => expect(usePickedElementsStore.getState().chips[0].uploading).toBe(false));
}

describe('ChatView with picked elements', () => {
    it('shows the chip, then sends it as page_elements and in the bubble', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await pickSaveButton();
        const chips = screen.getByRole('list', { name: 'Picked elements' });
        expect(within(chips).getByText('button.primary "Save"')).toBeTruthy();

        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'make this button blue' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));

        await waitFor(() => expect(mockedApi.sendMessageStream).toHaveBeenCalled());
        const [, message, options] = mockedApi.sendMessageStream.mock.calls[0];
        expect(message).toBe('make this button blue');
        expect((options as api.SendExtras).pageElements).toEqual([wireSaveButton]);
        expect(usePickedElementsStore.getState().chips).toEqual([]);
        const sent = useChatStore.getState().messages.find((m) => m.role === 'user');
        expect(sent?.page_elements).toEqual([wireSaveButton]);
        await waitFor(() => expect(document.querySelector('.element-chips.is-sent [data-ref="p1"]')).toBeTruthy());
    });

    it('a message without picks sends no page_elements', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(mockedApi.sendMessageStream).toHaveBeenCalled());
        expect((mockedApi.sendMessageStream.mock.calls[0][2] as api.SendExtras).pageElements).toBeUndefined();
    });

    it('waits for the crop to upload before it can send', async () => {
        mockedApi.uploadFile.mockReturnValue(new Promise(() => {}) as never);
        render(<ChatView sessionId={SESSION.id} />);
        act(() => usePickedElementsStore.getState().applyPick({ run: 1, elements: [{ ...SAVE, crop: btoa('png') }] }));
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'make this button blue' } });
        const send = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement;
        expect(send.disabled).toBe(true);
        expect(send.title).toBe('A picked element is still uploading');
    });

    it('a part toggled off is left out of what is sent', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await pickSaveButton();
        const html = screen.getByRole('button', { name: 'HTML' });
        expect(html.getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(html);
        expect(html.getAttribute('aria-pressed')).toBe('false');
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'what is this' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(mockedApi.sendMessageStream).toHaveBeenCalled());
        expect((mockedApi.sendMessageStream.mock.calls[0][2] as api.SendExtras).pageElements?.[0].outerHTML).toBeNull();
    });
});
