// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ArrowUpRight } from 'lucide-react';
import type { StartCard } from '../types';
import { bracketParts, isMessageKey, type MessageKey, type Params } from '../i18n/registry';
import { enMessages } from '../i18n/locales/en';
import { tEn, useT, type Translate } from '../i18n/useT';
import './StartCards.css';

interface StartCardsProps {
    cards: StartCard[];
    onPick: (card: StartCard) => void;
    disabled?: boolean;
}

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

/** A label whose halves each stay on one line; a folder name never wraps into the other half. */
function Unbroken({ text }: { text: string }) {
    const parts = bracketParts(text);
    if (parts.length === 1) return <>{text}</>;
    return <>{parts.map((p, i) => <span key={i} className="start-card-part">{p}</span>)}</>;
}

/** Tasks to start from, for what this PC is set up to do. */
export function StartCards({ cards, onPick, disabled }: StartCardsProps) {
    const t = useT();
    if (cards.length === 0) return null;
    return (
        <ul className="start-cards" aria-label={t('newChat.cardsLabel')}>
            {cards.map((card) => {
                const { title, note } = cardText(card, t);
                return (
                    <li key={card.skill}>
                        <button
                            type="button"
                            className="start-card"
                            onClick={() => onPick(card)}
                            disabled={disabled}
                            title={card.needs_input ? `${card.prompt}…` : card.prompt}
                        >
                            <span className="start-card-title">
                                {title}
                                <ArrowUpRight size={13} aria-hidden="true" />
                            </span>
                            <span className="start-card-note"><Unbroken text={note} /></span>
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}
