// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentStep } from '@webui/types';
import { dropAnswerDrafts } from '@webui/utils/answerDrafts';
import { dropErrorsEchoedInAnswer, isErrorContent } from '@webui/utils/answerErrors.core';
import { SERVER_CANCELLED_TEXT, turnEnding, type TurnEnding } from '@webui/utils/turnEnding.core';

import type { Message } from '@/api/client.types';

export interface SavedActivity {
    /** The steps the reply's timeline shows; drafts of the answer and errors it repeats are left out. */
    steps: AgentStep[];
    /** How the turn ended short of an answer, if it did. */
    ended: TurnEnding | undefined;
    /** The reply stands for a failure (the web bubble's `isError`): it isn't read aloud. */
    isError: boolean;
}

const NO_STEPS: AgentStep[] = [];

/** What a saved assistant reply shows of its turn, read the way the Agent UI reads it. */
export function savedActivity(message: Message): SavedActivity {
    const dropped = dropErrorsEchoedInAnswer(dropAnswerDrafts(message.agentSteps ?? NO_STEPS, message.content), message.content);
    const isError = dropped.echoed || message.failed === true || isErrorContent(message.content);
    const failed = isError || dropped.endedBy !== null;
    const stopState = message.stopState ?? (message.content.trim() === SERVER_CANCELLED_TEXT ? 'stopped' : undefined);
    return { steps: dropped.steps, ended: turnEnding(message.content, stopState, failed), isError };
}
