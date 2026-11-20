// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StartCard } from '../types';
import { isMessageKey, type MessageKey, type Params } from '../i18n/registry';
import { enMessages } from '../i18n/locales/en';
import { tEn, type Translate } from '../i18n/translator';

/** The params a card's note was built from, read back from the server's English. */
function noteParams(card: StartCard): Params | undefined {
    if (card.skill === 'document-brief') {
        const n = /^(\d+) /.exec(card.note)?.[1];
        return n ? { n: Number(n) } : undefined;
    }
    if (card.skill === 'coding') {
        const folder = /^In (.+)$/.exec(card.note)?.[1];
        return folder ? { folder } : undefined;
    }
    return undefined;
}

/** One line of a card in the chosen language, or the server's own text when it isn't one we know. */
function cardLine(t: Translate, server: string, key: string, params?: Params): string {
    const one = `${key}.one`;
    const other = `${key}.other`;
    const plural = isMessageKey(one) && isMessageKey(other) && typeof params?.n === 'number';
    const k = plural ? ((params!.n === 1 ? one : other) as MessageKey) : key;
    if (!isMessageKey(k)) return server;
    // A note we couldn't read its params back from is shown as sent.
    if (enMessages[k].includes('{') && !params) return server;
    // Only a card whose English matches ours is translated; a new or reworded one shows as sent.
    return tEn(k, params) === server ? t(k, params) : server;
}

export function cardText(card: StartCard, t: Translate): { title: string; note: string } {
    return {
        title: cardLine(t, card.title, `card.${card.skill}.title`),
        note: cardLine(t, card.note, `card.${card.skill}.note`, noteParams(card)),
    };
}
