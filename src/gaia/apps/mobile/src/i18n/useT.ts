// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getLocales } from 'expo-localization';

import { DEFAULT_LOCALE, getLocale, isLocaleCode, setVariantPrefix, type Say } from '@webui/i18n/registry';
import { dateLocale, translator, type Translate } from '@webui/i18n/translator';

import { GaiaApiError, GaiaError, GaiaNetworkError } from '@/api/errors';
import { useLocaleStore } from '@/state/locale';

// The phone says "your PC" where the shared catalog says "this PC".
setVariantPrefix('mobileApp.yourPc.');

export { dateLocale, translator, type Translate };

export function deviceLocaleCode(): string {
    const code = getLocales()[0]?.languageCode;
    return isLocaleCode(code) ? code : DEFAULT_LOCALE;
}

export function describeError(error: unknown, t: Translate): string {
    if (error instanceof GaiaError) return t(error.messageKey, error.params);
    return error instanceof Error ? error.message : String(error);
}

/** For an error filled into another message: use inside `t.compose`, so each language half carries its own wording and the line is bracketed once. */
export function describeErrorWith(error: unknown, say: Say): string {
    if (error instanceof GaiaError) return say(error.messageKey, error.params);
    return error instanceof Error ? error.message : String(error);
}

/** Like `describeErrorWith`, but a PC's own reason (`GaiaApiError.detail`) goes in as sent. */
export function describeFailureWith(error: unknown, say: Say): string {
    if (error instanceof GaiaApiError) return error.detail;
    return describeErrorWith(error, say);
}

/** The raw cause for a Details disclosure; never part of the sentence a person reads. */
export function technicalDetail(error: unknown): string {
    if (error instanceof GaiaNetworkError) return `${error.params.url}: ${error.params.reason}`;
    return error instanceof Error ? error.message : String(error);
}

/** The language picked in the app, else the phone's. */
export function useLocaleCode(): string {
    const picked = useLocaleStore((s) => s.locale);
    return picked && isLocaleCode(picked) ? picked : deviceLocaleCode();
}

export function useT(): Translate {
    return translator(useLocaleCode());
}

export function useDirection(): 'ltr' | 'rtl' {
    return getLocale(useLocaleCode()).meta.dir;
}
