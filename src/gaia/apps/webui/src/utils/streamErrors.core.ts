// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ApiError, parseErrorDetail, StreamTimeoutError, type StreamDroppedError } from '../services/apiErrors';
import type { Translate } from '../i18n/translator';

/** How many times a turn's lost stream is re-attached before the view gives up. */
export const MAX_STREAM_RECONNECTS = 3;

/** Wait before each re-attach: the first is immediate, later ones span a backend restart (~13 s in all). */
export const RECONNECT_DELAYS_MS = [0, 3_000, 10_000];

/** A re-attach found neither the run nor its saved answer. */
export const RUN_LOST_TEXT = 'The answer to your last message was lost: its run stopped before it finished, '
    + 'most often because GAIA restarted. Send the message again.';

/** The connection died while the run may still be going server-side, so re-attaching can recover it. */
export function isStreamInterrupted(err: Error): boolean {
    return err.name === 'StreamStalledError' || err.name === 'StreamDroppedError';
}

/** The last re-attach never reached GAIA, so no run there can still be answering. */
export function isGaiaUnreachable(err: Error): boolean {
    return err.name === 'StreamDroppedError' && (err as StreamDroppedError).unreachable === true;
}

/** The view lost the turn, but its run may still finish server-side; resending would ask twice. */
export function mayStillFinish(err: Error): boolean {
    if (isGaiaUnreachable(err)) return false;
    return isStreamInterrupted(err) || err instanceof StreamTimeoutError;
}

/**
 * Plain words and a next step for a chat-stream failure the client can name,
 * or null to leave the message to the caller's generic handling.
 */
export function describeStreamError(err: Error, t: Translate): string | null {
    if (isGaiaUnreachable(err)) return t('chatError.gaiaStopped');
    if (isStreamInterrupted(err)) return t('chatError.liveViewLost', { n: MAX_STREAM_RECONNECTS + 1 });
    if (err instanceof StreamTimeoutError) {
        if (err.endpoint === 'send') {
            return `GAIA did not start this answer within ${Math.round(err.timeoutMs / 1000)} seconds; it may be busy or stuck. `
                + 'If the chat list shows this chat running, reopen it later to see the answer. Otherwise, send the message again.';
        }
        return 'GAIA did not respond when this chat reconnected to its running answer. The answer may still finish: '
            + 'reopen this chat once GAIA is answering again.';
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

export type ChatErrorKind = 'stream' | 'context' | 'model_server' | 'connection' | 'timeout' | 'unknown';

const CONTEXT_RE = /context (window|size|length|limit)|exceeds the available context|maximum context|too many tokens/i;
const MODEL_SERVER_RE = /lemonade|\bLLM\b|could not get response/i;
const CONNECTION_RE = /failed to fetch|networkerror|\bfetch\b/i;
const TIMEOUT_RE = /timed out|timeout/i;

/** The id a failed answer names its saved problem report by (`RECORD_ID_RE` in failure_record.py). */
const FAILURE_RECORD_RE = /\bGF-\d{8}-\d{6}-[0-9a-f]{4}\b/;

export function failureRecordId(text: string): string | null {
    return FAILURE_RECORD_RE.exec(text)?.[0] ?? null;
}

/**
 * What a failed chat turn tells the user: the cause in plain words and what to
 * do next. Never the raw error text, which goes to the console log instead,
 * except the name of the problem report the server saved for it.
 */
export function classifyChatError(err: Error, t: Translate): { kind: ChatErrorKind; text: string } {
    const cause = chatErrorCause(err, t);
    const record = failureRecordId(err.message || '');
    return record ? { ...cause, text: `${cause.text}\n\n${t('chatError.savedReport', { id: record })}` } : cause;
}

function chatErrorCause(err: Error, t: Translate): { kind: ChatErrorKind; text: string } {
    const described = describeStreamError(err, t);
    if (described) return { kind: 'stream', text: described };
    const msg = err.message || '';
    if (CONTEXT_RE.test(msg)) return { kind: 'context', text: t('chatError.context') };
    if (MODEL_SERVER_RE.test(msg)) return { kind: 'model_server', text: t('chatError.modelServer') };
    if (err instanceof TypeError || CONNECTION_RE.test(msg)) return { kind: 'connection', text: t('chatError.connection') };
    if (TIMEOUT_RE.test(msg)) return { kind: 'timeout', text: t('chatError.timeout') };
    return { kind: 'unknown', text: t('chatError.unknown') };
}
