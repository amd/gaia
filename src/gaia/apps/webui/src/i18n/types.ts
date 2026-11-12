// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { enMessages } from './locales/en';

/** Every interface string, keyed by name; English (`locales/en.ts`) defines the set. */
export type MessageKey = keyof typeof enMessages;

/** A complete dictionary: tsc rejects a locale with a missing or misspelled key. */
export type Messages = { readonly [K in MessageKey]: string };

/** Keys `x.one` + `x.other` pair up as the plural message `x`. */
export type PluralKey = {
    [K in MessageKey]: K extends `${infer B}.one` ? (`${B}.other` extends MessageKey ? B : never) : never;
}[MessageKey];

export interface LocaleMeta {
    /** BCP-47 tag; written to `<html lang>`. */
    code: string;
    /** The language in its own script, as the Settings list shows it. */
    nativeName: string;
    englishName: string;
    /** Written to `<html dir>`; `rtl` mirrors the whole layout. */
    dir: 'ltr' | 'rtl';
    /** Render each label as "<translation> (<English>)", for audiences that read both. */
    showEnglishInBrackets: boolean;
}

export interface Locale {
    meta: LocaleMeta;
    messages: Messages;
}
