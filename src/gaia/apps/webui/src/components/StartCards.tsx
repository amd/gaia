// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ArrowUpRight } from 'lucide-react';
import type { StartCard } from '../types';
import { bracketParts } from '../i18n/registry';
import { useT } from '../i18n/useT';
import { cardText } from './startCardText';
import './StartCards.css';

interface StartCardsProps {
    cards: StartCard[];
    onPick: (card: StartCard) => void;
    disabled?: boolean;
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
