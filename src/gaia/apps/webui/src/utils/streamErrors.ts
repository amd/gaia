// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getT, type Translate } from '../i18n/useT';
import { classifyChatError as classify, describeStreamError as describe, type ChatErrorKind } from './streamErrors.core';

export * from './streamErrors.core';

/** `describeStreamError` in the interface language chosen now, unless `t` is given. */
export function describeStreamError(err: Error, t: Translate = getT()): string | null {
    return describe(err, t);
}

/** `classifyChatError` in the interface language chosen now, unless `t` is given. */
export function classifyChatError(err: Error, t: Translate = getT()): { kind: ChatErrorKind; text: string } {
    return classify(err, t);
}
