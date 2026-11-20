// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { compose, DEFAULT_LOCALE, pluralKey, translate, translateBare, withHint, type MessageKey, type Params, type PluralKey, type Say } from './registry';

export interface Translate {
    (key: MessageKey, params?: Params): string;
    /** `base.one` or `base.other` for `n`, with `{n}` filled in. */
    plural: (base: PluralKey, n: number, params?: Params) => string;
    /** "<label> (<hint>)" for a shortcut or size after a translated label, e.g. "Send (Enter)". */
    hint: (label: string, hint: string) => string;
    /** A line built from several messages, bracketed once; see `compose` in registry.ts. */
    compose: (build: (say: Say, code: string) => string) => string;
    /** The chosen language only, no English brackets; for connecting words inside a line. */
    bare: (key: MessageKey, params?: Params) => string;
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
        fn.compose = (build) => compose(code, build);
        fn.bare = (key, params) => translateBare(code, key, params);
        fn.locale = code;
        t = fn;
        cache.set(code, t);
    }
    return t;
}

/** English, for helpers whose callers pass no translator. */
export const tEn: Translate = translator('en');

/** Dates in the interface language with Western digits; English follows the PC's own region. */
export function dateLocale(t: Translate): string | undefined {
    return t.locale === DEFAULT_LOCALE ? undefined : `${t.locale}-u-nu-latn`;
}
