// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ArrowUpRight } from 'lucide-react';
import type { StartCard } from '../types';
import './StartCards.css';

interface StartCardsProps {
    cards: StartCard[];
    onPick: (card: StartCard) => void;
    disabled?: boolean;
}

/** Tasks to start from, for what this PC is set up to do. */
export function StartCards({ cards, onPick, disabled }: StartCardsProps) {
    if (cards.length === 0) return null;
    return (
        <ul className="start-cards" aria-label="Things to try">
            {cards.map((card) => (
                <li key={card.skill}>
                    <button
                        type="button"
                        className="start-card"
                        onClick={() => onPick(card)}
                        disabled={disabled}
                        title={card.needs_input ? `${card.prompt}…` : card.prompt}
                    >
                        <span className="start-card-title">
                            {card.title}
                            <ArrowUpRight size={13} aria-hidden="true" />
                        </span>
                        <span className="start-card-note">{card.note}</span>
                    </button>
                </li>
            ))}
        </ul>
    );
}
