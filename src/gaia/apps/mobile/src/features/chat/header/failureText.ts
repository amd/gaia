// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaError } from '@/api/errors';
import { requestFailureText, requestFailureWith } from '@/features/chat/chatErrors';
import type { Say } from '@webui/i18n/registry';
import type { Translate } from '@/i18n/useT';

/** Why a header action failed, as the `{error}` text of the web's `chat.*Failed` messages. */
export function failureReason(error: unknown, t: Translate): string {
    if (error instanceof GaiaError) return requestFailureText(error, t);
    return error instanceof Error ? error.message : String(error);
}

/** `failureReason` for a `{error}` inside `t.compose`. */
export function failureReasonWith(error: unknown, say: Say): string {
    if (error instanceof GaiaError) return requestFailureWith(error, say);
    return error instanceof Error ? error.message : String(error);
}
