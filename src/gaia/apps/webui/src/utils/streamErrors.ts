// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ApiError, parseErrorDetail, SEND_HEADER_TIMEOUT_MS, StreamStalledError, StreamTimeoutError } from '../services/api';

/** How many times a turn's stalled stream is re-attached before the view gives up. */
export const MAX_STREAM_RECONNECTS = 3;

/** A re-attach found neither the run nor its saved answer. */
export const RUN_LOST_TEXT = 'The answer to your last message was lost: its run stopped before it finished, '
    + 'most often because GAIA restarted. Send the message again.';

/**
 * Plain words and a next step for a chat-stream failure the client can name,
 * or null to leave the message to the caller's generic handling.
 */
export function describeStreamError(err: Error): string | null {
    if (err instanceof StreamStalledError) {
        return 'Lost the live view of this answer: the connection to GAIA went silent '
            + `${MAX_STREAM_RECONNECTS + 1} times. The answer may still finish. Reopen this chat in a minute to see it.`;
    }
    if (err instanceof StreamTimeoutError) {
        if (err.timeoutMs === SEND_HEADER_TIMEOUT_MS) {
            return `GAIA did not start this answer within ${Math.round(err.timeoutMs / 1000)} seconds; it may be busy or stuck. `
                + 'If the chat list shows this chat running, reopen it later to see the answer. Otherwise, send the message again.';
        }
        return 'GAIA did not answer when this view tried to show the running answer. '
            + 'Check that GAIA is still running, then reopen this chat.';
    }
    if (err instanceof ApiError) {
        const detail = err.detail ? parseErrorDetail(err.detail) : '';
        if (err.httpStatus === 429) {
            return 'GAIA is busy with another chat, so this message was not sent. '
                + 'Send it again when that chat finishes.';
        }
        if (err.httpStatus === 409) {
            return detail || 'This chat is already answering a message. Wait for it to finish, or press Stop.';
        }
        return `GAIA could not take this message (HTTP ${err.httpStatus})${detail ? `: ${detail}` : '.'}`;
    }
    return null;
}
