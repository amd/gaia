// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ApiError } from '../services/api';
import { ref, type Param } from '../i18n/registry';
import { getT, type Translate } from '../i18n/useT';

const NETWORK_ERROR_RE = /failed to fetch|networkerror|load failed/i;

/** fetch() could not reach GAIA at all: its process is gone or not listening. */
export function isNetworkError(err: unknown): boolean {
    return err instanceof TypeError && NETWORK_ERROR_RE.test(err.message);
}

/** Why a request failed, as an `{error}` param: never the browser's raw "Failed to fetch". */
export function requestErrorParam(err: unknown): Param {
    if (isNetworkError(err)) return ref('request.gaiaUnreachable');
    if (err instanceof ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

/** The same reason as a line of its own. */
export function requestErrorText(err: unknown, t: Translate = getT()): string {
    const reason = requestErrorParam(err);
    return typeof reason === 'object' ? t(reason.ref, reason.params) : String(reason);
}
