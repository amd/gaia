// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/useT';
import { localizeContextReason } from './liveStatus';

/** Why the context endpoint has no figure, as the backend sent it. */
export interface ContextUnavailable {
    reason: string;
    code?: string;
}

/**
 * Reasons are keyed under `ctx.reason.<code>`. A reason with no code is an agent's
 * own; one GAIA knows is translated, any other, or one whose English no longer
 * matches the backend's, shows as sent.
 */
export function contextReason(t: Translate, { reason, code }: ContextUnavailable): string {
    const key = `ctx.reason.${code}`;
    if (!code) return localizeContextReason(t, reason);
    if (!isMessageKey(key) || tEn(key) !== reason) return reason;
    return t(key);
}
