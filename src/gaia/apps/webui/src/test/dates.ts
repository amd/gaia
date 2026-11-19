// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act } from '@testing-library/react';
import { useChatStore } from '../stores/chatStore';

/** A short month name as Intl writes it in Arabic. */
export const ARABIC_MONTH = /يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر/;
export const ENGLISH_MONTH = /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/;

/** Switch the interface language for one test; pair with `resetUiLocale` in afterEach. */
export function setUiLocale(code: string): void {
    act(() => useChatStore.getState().setLocale(code));
}

export function resetUiLocale(): void {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
}
