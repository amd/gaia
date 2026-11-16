// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';

import type {
    ConfirmToolResult,
    CreateSessionRequest,
    DocumentResponse,
    GaiaClient,
    PickedFile,
    Session,
    SessionList,
    ToolConfirmRequest,
    UserInputRequest,
    UserInputResult,
} from '@/api/client.types';
import { markPromptResolved, permissionAnswered, questionAnswered, readTurn, updateTurn } from '@/state/chatStreams';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

/** Allows or denies the tool call a turn is blocked on; its prompt closes once GAIA has the answer. */
export async function confirmPermission(client: GaiaClient, request: ToolConfirmRequest): Promise<ConfirmToolResult> {
    const result = await client.confirmTool(request);
    if (request.confirm_id) markPromptResolved(request.session_id, request.confirm_id, { state: 'answered' });
    updateTurn(request.session_id, (turn) => permissionAnswered(turn, request.confirm_id));
    return result;
}

/** Answers the question a turn is blocked on; the question closes once GAIA has the answer. */
export async function answerQuestion(client: GaiaClient, request: UserInputRequest): Promise<UserInputResult> {
    const pending = readTurn(request.session_id).pendingQuestion;
    const sensitive = pending?.request_id === request.request_id && !!pending.sensitive;
    const result = await client.sendUserInput(request);
    const answer = request.display ?? (typeof request.value === 'string' ? request.value : undefined);
    markPromptResolved(request.session_id, request.request_id, {
        state: 'answered',
        ...(answer !== undefined && !sensitive ? { answer } : null),
    });
    updateTurn(request.session_id, (turn) => questionAnswered(turn, request.request_id));
    return result;
}

export function useCreateSession() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Session, Error, CreateSessionRequest | void>({
        mutationFn: (request) => requireClient(client).createSession(request ?? {}),
        onSuccess: (session) => {
            queryClient.setQueryData(queryKeys.session(session.id), session);
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

/** `mutate({ session_id, approved, always?, confirm_id })` answers `pendingPermission`. */
export function useConfirmTool() {
    const client = useGaiaClient();
    return useMutation<ConfirmToolResult, Error, ToolConfirmRequest>({
        mutationFn: (request) => confirmPermission(requireClient(client), request),
    });
}

/** `mutate({ session_id, request_id, value })` answers `pendingQuestion`. */
export function useAnswerQuestion() {
    const client = useGaiaClient();
    return useMutation<UserInputResult, Error, UserInputRequest>({
        mutationFn: (request) => answerQuestion(requireClient(client), request),
    });
}

interface Previous {
    list: SessionList | undefined;
    one: Session | undefined;
}

/** Shows a change to a chat at once; the returned snapshot puts it back. */
async function applyToSession(queryClient: QueryClient, id: string, changes: Partial<Session>): Promise<Previous> {
    await queryClient.cancelQueries({ queryKey: queryKeys.sessions });
    const previous: Previous = {
        list: queryClient.getQueryData<SessionList>(queryKeys.sessions),
        one: queryClient.getQueryData<Session>(queryKeys.session(id)),
    };
    queryClient.setQueryData<SessionList>(queryKeys.sessions, (list) =>
        list && { ...list, sessions: list.sessions.map((s) => (s.id === id ? { ...s, ...changes } : s)) },
    );
    queryClient.setQueryData<Session>(queryKeys.session(id), (one) => one && { ...one, ...changes });
    return previous;
}

function restoreSession(queryClient: QueryClient, id: string, previous: Previous | undefined) {
    if (!previous) return;
    if (previous.list) queryClient.setQueryData(queryKeys.sessions, previous.list);
    if (previous.one) queryClient.setQueryData(queryKeys.session(id), previous.one);
}

function refreshSession(queryClient: QueryClient, id: string) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
    void queryClient.invalidateQueries({ queryKey: queryKeys.session(id) });
}

/** `mutate({ sessionId, title })` renames a chat and pins the title against the auto-retitler (#2165); shown at once, put back on failure. */
export function useRenameSession() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Session, Error, { sessionId: string; title: string }, Previous>({
        mutationFn: ({ sessionId, title }) =>
            requireClient(client).updateSession(sessionId, { title, title_is_custom: true }),
        onMutate: ({ sessionId, title }) => applyToSession(queryClient, sessionId, { title, title_is_custom: true }),
        onError: (_error, { sessionId }, previous) => restoreSession(queryClient, sessionId, previous),
        onSettled: (_data, _error, { sessionId }) => refreshSession(queryClient, sessionId),
    });
}

/** `mutate({ id, pinned })`; the chat moves to or from the Pinned group at once. */
export function useSetPinned() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Session, Error, { id: string; pinned: boolean }, Previous>({
        mutationFn: ({ id, pinned }) => requireClient(client).setSessionPinned(id, pinned),
        onMutate: ({ id, pinned }) => applyToSession(queryClient, id, { pinned }),
        onError: (_error, { id }, previous) => restoreSession(queryClient, id, previous),
        onSettled: (_data, _error, { id }) => refreshSession(queryClient, id),
    });
}

/** `mutate(id)`; `onDeletedOpenChat` runs when `id` is the open chat, so the caller can leave its screen. */
export function useDeleteSession(openId?: string | null, onDeletedOpenChat?: () => void) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<unknown, Error, string>({
        mutationFn: (id) => requireClient(client).deleteSession(id),
        onSuccess: (_data, id) => {
            // The open chat's screen would refetch a chat that is gone.
            queryClient.removeQueries({ queryKey: queryKeys.session(id) });
            queryClient.removeQueries({ queryKey: queryKeys.messages(id) });
            if (id === openId) onDeletedOpenChat?.();
        },
        onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sessions }),
    });
}

/** `mutate(id)` clears a chat's unread mark; a failure is not undone, or the mark would re-fire it. */
export function useMarkRead() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Session, Error, string>({
        mutationFn: (id) => requireClient(client).markSessionRead(id),
        onMutate: (id) => applyToSession(queryClient, id, { unread: false }),
        onSuccess: (_session, id) => refreshSession(queryClient, id),
    });
}

/** `mutate(sessionId)` flips the chat's private mode. */
export function useTogglePrivate() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Session, Error, string>({
        mutationFn: (sessionId) => requireClient(client).toggleSessionPrivate(sessionId),
        onSuccess: (session) => {
            queryClient.setQueryData(queryKeys.session(session.id), session);
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

/** `mutate({ sessionId, docId })` takes a document out of the chat; the library keeps it. */
export function useDetachDocument() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<void, Error, { sessionId: string; docId: string }>({
        mutationFn: ({ sessionId, docId }) => requireClient(client).detachDocument(sessionId, docId),
        onSuccess: (_result, { sessionId }) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.session(sessionId) });
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

/** `mutate({ sessionId, docId })` attaches an indexed library document to the chat. */
export function useAttachDocument() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<void, Error, { sessionId: string; docId: string }>({
        mutationFn: ({ sessionId, docId }) => requireClient(client).attachDocument(sessionId, docId),
        onSuccess: (_result, { sessionId }) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.session(sessionId) });
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

export interface AddDocumentRequest {
    file: PickedFile;
    /** The chat to attach to once indexed; null leaves it in the library for the next new chat. */
    chatId: string | null;
}

export interface AddDocumentResult {
    document: DocumentResponse;
    /** Set when the document was indexed but the chat refused it; the library still has it. */
    attachError: Error | null;
}

/** `mutate({ file, chatId })` uploads a picked file to the library and attaches it to the chat, as the web library does. */
export function useAddDocument() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<AddDocumentResult, Error, AddDocumentRequest>({
        mutationFn: async ({ file, chatId }) => {
            const api = requireClient(client);
            const document = await api.uploadDocument(file);
            // A file GAIA couldn't read has a library row with its reason; a chat must not be promised it.
            if (!chatId || document.indexing_status === 'failed') return { document, attachError: null };
            try {
                await api.attachDocument(chatId, document.id);
            } catch (err) {
                return { document, attachError: err instanceof Error ? err : new Error(String(err)) };
            }
            return { document, attachError: null };
        },
        onSettled: (_result, _error, { chatId }) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.documents });
            if (chatId) {
                void queryClient.invalidateQueries({ queryKey: queryKeys.session(chatId) });
                void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
            }
        },
    });
}
