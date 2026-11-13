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
