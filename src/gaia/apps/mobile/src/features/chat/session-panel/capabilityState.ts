// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError } from '@/api/errors';
import { failureReasonWith } from '@/features/chat/header/failureText';
import type { MessageKey } from '@webui/i18n/registry';
import type { Translate } from '@/i18n/useT';

export type CapabilityFailure = { notRunning: true } | { notRunning: false; text: string };

/** 409 is only ever "the agent hasn't started yet", every new chat's first state. */
export function capabilityFailure(error: unknown, t: Translate, failedKey: MessageKey): CapabilityFailure {
    if (error instanceof GaiaApiError && error.status === 409) return { notRunning: true };
    return { notRunning: false, text: t.compose((say) => say(failedKey, { error: failureReasonWith(error, say) })) };
}
