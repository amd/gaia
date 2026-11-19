// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey, ref, type Param, type Params } from '../i18n/registry';
import type { Translate } from '../i18n/useT';

// Must match USER_ERROR_MARK in services/auto-updater.cjs.
const USER_ERROR_MARK = 'gaia-update-error:';

interface CodedError {
    code: string;
    params?: Params;
    note?: { code: string; params?: Params };
    message?: string;
}

function coded(code: string, params?: Params): Param | null {
    const key = `update.err.${code}`;
    return isMessageKey(key) ? ref(key, params ?? {}) : null;
}

/**
 * Why an updater call failed, as a message param: in the user's language when
 * the updater named the reason, otherwise its own words without Electron's
 * "Error invoking remote method" prefix.
 */
export function updateErrorReason(err: unknown): Param {
    const text = err instanceof Error ? err.message : String(err);
    const at = text.indexOf(USER_ERROR_MARK);
    if (at < 0) return text.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '');
    let parsed: CodedError;
    try {
        parsed = JSON.parse(text.slice(at + USER_ERROR_MARK.length)) as CodedError;
    } catch {
        return text.slice(0, at) + text.slice(at + USER_ERROR_MARK.length);
    }
    const error = coded(parsed.code, parsed.params) ?? parsed.message ?? parsed.code;
    const note = parsed.note && coded(parsed.note.code, parsed.note.params);
    return note ? ref('update.err.withNote', { error, note }) : error;
}

/** `updateErrorReason` as a line of text, e.g. for a tooltip. */
export function updateErrorText(err: unknown, t: Translate): string {
    const reason = updateErrorReason(err);
    return typeof reason === 'object' ? t(reason.ref, reason.params) : String(reason);
}
