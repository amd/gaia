// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getT, type Translate } from '../i18n/useT';
import { requestErrorText as text } from './requestErrors.core';

export * from './requestErrors.core';

/** `requestErrorText` in the interface language chosen now, unless `t` is given. */
export function requestErrorText(err: unknown, t: Translate = getT()): string {
    return text(err, t);
}
