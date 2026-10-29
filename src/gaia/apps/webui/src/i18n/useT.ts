// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../stores/chatStore';
import { pluralKey, translate, withHint, type MessageKey, type Params, type PluralKey } from './registry';

export interface Translate {
    (key: MessageKey, params?: Params): string;
    /** `base.one` or `base.other` for `n`, with `{n}` filled in. */
    plural: (base: PluralKey, n: number, params?: Params) => string;
    /** "<label> (<hint>)" for a shortcut or size after a translated label, e.g. "Send (Enter)". */
    hint: (label: string, hint: string) => string;
    locale: string;
}

const cache = new Map<string, Translate>();

/** One stable translator per locale, so it is safe in hook dependency lists. */
export function translator(code: string): Translate {
    let t = cache.get(code);
    if (!t) {
        const fn = ((key: MessageKey, params?: Params) => translate(code, key, params)) as Translate;
        fn.plural = (base, n, params) => translate(code, pluralKey(base, n), { n, ...params });
        fn.hint = (label, hint) => withHint(code, label, hint);
        fn.locale = code;
        t = fn;
        cache.set(code, t);
    }
    return t;
}

/** The translator for the chosen interface language; re-renders when it changes. */
export function useT(): Translate {
    return translator(useChatStore((s) => s.locale));
}

/** Outside React (module helpers, event handlers): the language chosen right now. */
export function getT(): Translate {
    return translator(useChatStore.getState().locale);
}

/** English, for helpers whose callers pass no translator. */
export const tEn: Translate = translator('en');
