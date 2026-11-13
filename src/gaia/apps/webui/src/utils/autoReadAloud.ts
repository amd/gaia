// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { dropErrorsEchoedInAnswer, isErrorContent } from '../components/MessageBubble';
import { MAX_SPEECH_CHARS } from '../services/speechApi';
import type { Message } from '../types';
import { speechPieces } from './speechText';

/** Whether "Read each reply aloud" should speak *reply* as its turn ends.
 *  A failed turn's text is its error, which the person reads, not hears. */
export function readsAloudWhenDone(reply: Message): boolean {
    const content = reply.content.trim();
    if (!content || reply.stopState || reply.failed || reply.failure) return false;
    if (isErrorContent(content)) return false;
    if (speechPieces(content, MAX_SPEECH_CHARS).length === 0) return false;
    return !dropErrorsEchoedInAnswer(reply.agentSteps ?? [], content).echoed;
}
