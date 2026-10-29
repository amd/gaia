// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Interface languages.
 *
 * To add a language:
 *   1. Copy `locales/ar.ts` to `locales/<code>.ts` and translate every message.
 *      It is typed `Messages`, so tsc fails on a missing or misspelled key, and
 *      `__tests__/i18n.test.tsx` checks every registered locale against English.
 *   2. Fill in its `meta`: BCP-47 `code`, `nativeName`, `englishName`, `dir`
 *      ('rtl' mirrors the layout), and `showEnglishInBrackets`.
 *   3. Import it and add it to `LOCALES` below.
 * The Settings > General language list and `<html lang dir>` pick it up; no
 * component changes.
 *
 * A new string goes into `locales/en.ts` first, then into every other locale.
 */

import { ar } from './locales/ar';
import { en } from './locales/en';
import type { Locale, LocaleMeta, MessageKey, PluralKey } from './types';

export type { Locale, LocaleMeta, MessageKey, PluralKey } from './types';

/** Registered locales, in the order Settings lists them. English is the source and comes first. */
export const LOCALES: readonly Locale[] = [en, ar];

export const DEFAULT_LOCALE = en.meta.code;

const BY_CODE = new Map(LOCALES.map((l) => [l.meta.code, l]));

export function isLocaleCode(code: unknown): code is string {
    return typeof code === 'string' && BY_CODE.has(code);
}

export function getLocale(code: string): Locale {
    const locale = BY_CODE.get(code);
    if (!locale) {
        throw new Error(
            `Unknown UI locale "${code}". Registered: ${[...BY_CODE.keys()].join(', ')}. ` +
                'Add it to LOCALES in src/i18n/registry.ts.',
        );
    }
    return locale;
}

// Unicode isolates: LRI / RLI open a left-to-right / right-to-left island, PDI closes it.
const LRI = '\u2066';
const RLI = '\u2067';
const FSI = '\u2068';
const PDI = '\u2069';
const ISOLATES = /[\u2066-\u2069]/g;

/** `text` as its own bidi island, so neighbouring text can't reorder its brackets and punctuation. */
export function isolate(text: string, dir: 'ltr' | 'rtl'): string {
    return `${dir === 'rtl' ? RLI : LRI}${text}${PDI}`;
}

/** `text` without bidi isolates, for comparing what a label says. */
export function stripIsolates(text: string): string {
    return text.replace(ISOLATES, '');
}

/** "<label> (<hint>)", the hint kept whole when the label reads right to left. */
export function withHint(code: string, label: string, hint: string): string {
    return getLocale(code).meta.dir === 'rtl' ? `${label} ${isolate(`(${hint})`, 'ltr')}` : `${label} (${hint})`;
}

/** How Settings lists a locale: "English", "العربية (Arabic)". */
export function localeOptionLabel(meta: LocaleMeta): string {
    if (meta.nativeName === meta.englishName) return meta.nativeName;
    return meta.dir === 'rtl'
        ? `${isolate(meta.nativeName, 'rtl')} ${isolate(`(${meta.englishName})`, 'ltr')}`
        : `${meta.nativeName} (${meta.englishName})`;
}

/** A message used as a param, so each language fills in its own wording of it. */
export interface MessageRef {
    readonly ref: MessageKey;
    readonly params?: Params;
}

export const ref = (key: MessageKey, params?: Params): MessageRef => ({ ref: key, params });

export type Param = string | number | MessageRef;
export type Params = { readonly [name: string]: Param };

/** A message in one language with no English brackets, for assembling a longer line. */
export type Say = (key: MessageKey, params?: Params) => string;

/** `isolateText`: wrap string params (names, folders, paths) so their own direction can't spill into the label. */
function fill(locale: Locale, key: MessageKey, params: Params | undefined, isolateText = false): string {
    return locale.messages[key].replace(/\{(\w+)\}/g, (_, name: string) => {
        const value = params?.[name];
        if (value === undefined) {
            throw new Error(`Message "${key}" needs a {${name}} param, and the caller passed none.`);
        }
        if (typeof value === 'object') return fill(locale, value.ref, value.params, isolateText);
        return isolateText && typeof value === 'string' ? `${FSI}${value}${PDI}` : String(value);
    });
}

/**
 * A message in `code`; a bracketed locale renders "<translation> (<English>)".
 * Each half is a bidi isolate: unwrapped, an RTL paragraph pulls the English's
 * brackets and final punctuation to the wrong end ("Good) (?evening").
 */
export function translate(code: string, key: MessageKey, params?: Params): string {
    return compose(code, (say) => say(key, params));
}

/**
 * A line `build` assembles from several messages, written once per language and
 * bracketed once: "فهرس المستندات، استعلم في ملف (Indexed documents, queried file)",
 * not a bracket after every piece.
 */
export function compose(code: string, build: (say: Say) => string): string {
    const locale = getLocale(code);
    const rtl = locale.meta.dir === 'rtl';
    const own = build((key, params) => fill(locale, key, params, rtl));
    if (!locale.meta.showEnglishInBrackets || locale === en) return own;
    const english = build((key, params) => fill(en, key, params, rtl));
    if (own === english) return own;
    return `${isolate(own, locale.meta.dir)}${BRACKET_JOIN}${isolate(`(${english})`, 'ltr')}`;
}

const BRACKET_JOIN = ' ';

/**
 * A bracketed label as its two halves, so a renderer can keep each on one line
 * and wrap only between them; one part when the label isn't bracketed.
 */
export function bracketParts(label: string): string[] {
    const at = label.indexOf(`${PDI}${BRACKET_JOIN}${LRI}(`);
    return at < 0 ? [label] : [label.slice(0, at + 1), label.slice(at + 1 + BRACKET_JOIN.length)];
}

/** The `.one` / `.other` half of a plural message, by the English rule. */
export function pluralKey(base: PluralKey, n: number): MessageKey {
    return `${base}.${n === 1 ? 'one' : 'other'}` as MessageKey;
}

export function isMessageKey(key: string): key is MessageKey {
    return Object.prototype.hasOwnProperty.call(en.messages, key);
}

/** Single writer for `<html lang dir>`, so the layout direction always matches the language. */
export function applyLocale(code: string): void {
    const { meta } = getLocale(code);
    document.documentElement.lang = meta.code;
    document.documentElement.dir = meta.dir;
}
