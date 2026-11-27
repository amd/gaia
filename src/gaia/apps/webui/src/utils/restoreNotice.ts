// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey, isolate, ref, type Param } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/useT';

/** A failed restore as the backend sent it: the English line, and its parts when it has them. */
export interface RestoreFailure {
    message: string;
    model?: string;
    reason?: string;
    reasonCode?: string | null;
}

/**
 * Reasons are keyed under `model.restore.reason.<code>`. A line with no parts, or whose
 * English no longer matches the backend's, shows as sent, kept left to right so an
 * Arabic page can't move its final period to the front.
 */
export function restoreFailedText(t: Translate, { message, model, reason, reasonCode }: RestoreFailure): string {
    if (!model || !reason || tEn('model.restore.failed', { model, reason }) !== message) return isolate(message, 'ltr');
    const key = `model.restore.reason.${reasonCode}`;
    const why: Param = reasonCode && isMessageKey(key) && tEn(key) === reason ? ref(key) : reason;
    return t('model.restore.failed', { model, reason: why });
}
