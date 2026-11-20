// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo } from 'react';

import type { GaiaNotification } from '@webui/types/agent';
import { repeatedAllowCount } from '@webui/utils/permissionPrompts.core';
import { permissionRequestNotification } from '@webui/utils/toolEvents.core';

import { useConfirmTool } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { deadlineFor, recordAnsweredPrompt, useAnsweredPrompts } from '@/state/permissionPrompts';
import { markPromptResolved, permissionAnswered, updateTurn, useChatStreamStore } from '@/state/chatStreams';

export type PermissionChoice = 'once' | 'always' | 'deny';

export interface PermissionPromptState {
    prompt: GaiaNotification;
    repeats: number;
    busy: boolean;
    error: string | null;
    onAnswer(choice: PermissionChoice): void;
    onExpired(): void;
}

/** The tool approval the chat's turn waits on, or null. Feeds the permission card's props. */
export function usePermissionPrompt(sessionId: string): PermissionPromptState | null {
    const t = useT();
    const event = useChatStreamStore((state) => state.turns[sessionId]?.pendingPermission ?? null);
    const settled = useChatStreamStore((state) => (event?.confirm_id ? state.resolved[sessionId]?.[event.confirm_id] : undefined));
    const history = useAnsweredPrompts(sessionId);
    const confirm = useConfirmTool();

    const prompt = useMemo(() => {
        if (!event || settled) return null;
        const built = permissionRequestNotification(event, sessionId, [...history], t);
        if (built.expiresAt === undefined && built.timeoutSeconds && built.timeoutSeconds > 0) {
            return { ...built, expiresAt: deadlineFor(sessionId, built.id, built.timeoutSeconds) };
        }
        return built;
    }, [event, settled, sessionId, history, t]);

    if (!prompt) return null;

    const onAnswer = (choice: PermissionChoice) => {
        confirm.mutate(
            { session_id: sessionId, approved: choice !== 'deny', always: choice === 'always', confirm_id: prompt.confirmId },
            { onSuccess: () => recordAnsweredPrompt(sessionId, prompt, choice === 'deny' ? 'deny' : 'allow') },
        );
    };
    // The backend already denied it; nothing to send.
    const onExpired = () => {
        markPromptResolved(sessionId, prompt.id, { state: 'expired' });
        updateTurn(sessionId, (turn) => permissionAnswered(turn, prompt.confirmId));
    };
    const failed = confirm.isError && confirm.variables?.confirm_id === prompt.confirmId;

    return {
        prompt,
        repeats: repeatedAllowCount([...history], prompt),
        busy: confirm.isPending,
        error: failed ? describeError(confirm.error, t) : null,
        onAnswer,
        onExpired,
    };
}
