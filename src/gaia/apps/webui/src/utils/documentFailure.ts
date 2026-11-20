// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Translate } from '../i18n/useT';

/**
 * Why a document failed when the model server, not the file, was the problem,
 * or null for any other failure. The connection error stays in the log.
 */
export function serverFailureText(kind: string | null | undefined, t: Translate): string | null {
    if (kind === 'model_server_down') return t('docs.serverDown');
    if (kind === 'model_server_busy') return t('docs.serverBusy');
    return null;
}

/** True when GAIA retries the document by itself once the model server is back; a busy one waits on the user's Retry. */
export function waitsForServerReturn(kind: string | null | undefined): boolean {
    return kind === 'model_server_down';
}

/** True when the model server was too busy for the document, so it waits on the user's Retry. */
export function needsRetry(kind: string | null | undefined): boolean {
    return kind === 'model_server_busy';
}

/** The "couldn't be added" line for a model-server failure, or null for any other failure. */
export function serverNotAddedText(kind: string | null | undefined, name: string, t: Translate): string | null {
    if (kind === 'model_server_down') return t('docs.notAddedServer', { name });
    if (kind === 'model_server_busy') return t('docs.notAddedServerBusy', { name });
    return null;
}
