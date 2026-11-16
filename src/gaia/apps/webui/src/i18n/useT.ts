// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../stores/chatStore';
import { translator, type Translate } from './translator';

export { dateLocale, tEn, translator, type Translate } from './translator';

/** The translator for the chosen interface language; re-renders when it changes. */
export function useT(): Translate {
    return translator(useChatStore((s) => s.locale));
}

/** Outside React (module helpers, event handlers): the language chosen right now. */
export function getT(): Translate {
    return translator(useChatStore.getState().locale);
}
