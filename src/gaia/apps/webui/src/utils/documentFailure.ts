// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Translate } from '../i18n/translator';

/** True when a document failed because the model server, not the file, was the problem. */
export function isServerFailure(kind: string | null | undefined): boolean {
    return kind === 'model_server_down' || kind === 'model_server_busy';
}

/**
 * Why a document failed when the model server, not the file, was the problem,
 * or null for any other failure. The connection error stays in the log.
 */
export function serverFailureText(kind: string | null | undefined, t: Translate): string | null {
    if (kind === 'model_server_down') return t('docs.serverDown');
    if (kind === 'model_server_busy') return t('docs.serverBusy');
    return null;
}
