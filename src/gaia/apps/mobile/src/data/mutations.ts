// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQueryClient } from '@tanstack/react-query';

import type {
    ConfirmToolResult,
    CreateSessionRequest,
    GaiaClient,
    Session,
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
