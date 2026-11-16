// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isErrorContent } from '@webui/utils/answerErrors.core';
import { SERVER_CANCELLED_TEXT, splitCutOff } from '@webui/utils/turnEnding.core';

import type { Message, StreamEvent } from '@/api/client.types';
import type { GaiaError } from '@/api/errors';
import type { ChatStream } from '@/data';

import { mayStillFinish } from './chatErrors';
import type { Stop } from './EndingNotes';

export interface TurnTail {
    /** The turn is running: the answer streams and shows its live line. */
    live: boolean;
    text: string;
    events: readonly StreamEvent[];
    stop?: Stop;
    error?: GaiaError;
    /** The saved reply a retry replaces; null for one that was never saved. */
    replyId: number | null;
    canTryAgain: boolean;
    canContinue: boolean;
    /** The turn ended as a failed reply (GAIA said so, or the send failed for good). */
    failedReply?: boolean;
    /** The saved reply already shows the failure (its banner and log line), so the tail doesn't repeat it. */
    shownOnReply?: boolean;
    /** Show "Something went wrong": a failed reply whose saved copy doesn't already show it. */
    errorBanner?: boolean;
    /** The cause a failed turn named, for its fix. */
    failure?: string;
    policyAlert?: ChatStream['policyAlert'];
}

type Turn = Pick<
    ChatStream,
    'status' | 'liveText' | 'events' | 'error' | 'stopState' | 'stopError' | 'failed' | 'failure' | 'policyAlert'
>;

/**
 * What goes under the saved messages: the running turn, how the last one ended
 * (stopped, failed, cut off) and its Try again and Continue. Those two go on the
 * latest reply only, never while busy. Null when there is nothing to show.
 */
export function buildTail(messages: readonly Message[], turn: Turn): TurnTail | null {
    const busy = turn.status !== 'idle';
    const last = messages.filter((m) => m.role !== 'system').at(-1);
    const lastReply = last?.role === 'assistant' ? last : undefined;
    const cancelled = lastReply?.content.trim() === SERVER_CANCELLED_TEXT;
    const cutOff = lastReply ? splitCutOff(lastReply.content) : null;
    // A stop GAIA never confirmed has no saved reply: its text comes from the turn.
    const stopOnSaved = !busy && !!turn.stopState && !!lastReply && !(turn.stopState === 'unconfirmed' && turn.liveText);

    if (busy) {
        return {
            live: true,
            text: turn.liveText,
            events: turn.events,
            error: turn.error ?? undefined,
            replyId: null,
            canTryAgain: false,
            canContinue: false,
            policyAlert: turn.policyAlert,
        };
    }
    if (stopOnSaved && lastReply) {
        const stoppedCleanly = turn.stopState === 'stopped' && !turn.stopError;
        const hasText = lastReply.content.trim() !== '' && !cancelled && !cutOff;
        return {
            live: false,
            text: '',
            events: [],
            // The saved "Cancelled." reply already carries its own note.
            stop: cancelled ? undefined : { state: turn.stopState!, error: turn.stopError },
            replyId: lastReply.id,
            canTryAgain: stoppedCleanly,
            canContinue: stoppedCleanly && hasText,
            policyAlert: turn.policyAlert,
        };
    }
    if (turn.error || turn.liveText || turn.stopState || turn.failed || turn.policyAlert) {
        const failed = !!turn.error && !mayStillFinish(turn.error);
        const failedReply = failed || !!turn.failed;
        const stop: Stop | undefined = turn.stopState ? { state: turn.stopState, error: turn.stopError } : undefined;
        const shownOnReply = !!turn.failed && !turn.error && !!lastReply && (lastReply.failed === true || isErrorContent(lastReply.content));
        return {
            live: false,
            text: turn.liveText,
            events: turn.events,
            stop,
            error: turn.error ?? undefined,
            // A failed reply GAIA saved is the one a retry replaces.
            replyId: turn.failed && !turn.error && lastReply ? lastReply.id : null,
            canTryAgain: failedReply || (stop?.state === 'stopped' && !stop.error),
            canContinue: false,
            failedReply,
            shownOnReply,
            errorBanner: !!turn.failed && !turn.error && !shownOnReply,
            failure: turn.failure,
            policyAlert: turn.policyAlert,
        };
    }
    // A failed reply read back from GAIA keeps its Try again, as on the desktop.
    if (lastReply && (cutOff || cancelled || lastReply.failed === true)) {
        return { live: false, text: '', events: [], replyId: lastReply.id, canTryAgain: true, canContinue: false };
    }
    return null;
}

/** The saved reply whose failure the model-server fix under it explains, so the reply drops its own banner. */
export function serverDownReplyId(tail: TurnTail | null): number | null {
    return tail && !tail.live && tail.failure === 'model_server_down' ? tail.replyId : null;
}
