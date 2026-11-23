// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ArrowUpRight, ChefHat, Code, FileText, History, ListChecks, Mail, Receipt, ScrollText, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import './StartCards.css';
import { cardText } from './StartCards';
import { useT } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
import type { StartCard } from '../types';

interface TryExample {
    icon: LucideIcon;
    label: MessageKey;
    prompt: MessageKey;
}

/** Examples that each end in a different kind of card, none needing the web; the first is shown most. */
export const TRY_EXAMPLES: readonly TryExample[] = [
    { icon: ChefHat, label: 'cards.try.recipe', prompt: 'cards.try.recipePrompt' },
    { icon: ListChecks, label: 'cards.try.checklist', prompt: 'cards.try.checklistPrompt' },
    { icon: Receipt, label: 'cards.try.bill', prompt: 'cards.try.billPrompt' },
];

/** Starters in one row: the backend's tasks first, then examples to fill it. */
export const STARTERS_SHOWN = 3;
/** Backend tasks shown, so at least one starter still ends in a card. */
export const CARDS_SHOWN = 2;

const CARD_ICONS: Record<string, LucideIcon> = {
    'document-brief': FileText,
    'inbox-triage': Mail,
    'research-report': Search,
    coding: Code,
    summarize: ScrollText,
    'check-in': History,
};

interface TryChipsProps {
    /** Tasks for what this PC is set up to do (`/api/onboarding/start`). */
    cards?: StartCard[];
    /** Every pick fills the box to edit or send; none sends by itself. */
    onPick: (prompt: string) => void;
    disabled?: boolean;
    /** The tasks haven't arrived yet: hold the row's space, unseen, so the screen doesn't jump when they do. */
    pending?: boolean;
}

/** Up to three starters for an empty chat. */
export function TryChips({ cards = [], onPick, disabled, pending = false }: TryChipsProps) {
    const t = useT();
    const tasks = cards.slice(0, CARDS_SHOWN);
    const examples = TRY_EXAMPLES.slice(0, STARTERS_SHOWN - tasks.length);
    return (
        <div
            className={`try-chips${pending ? ' is-pending' : ''}`}
            role="group"
            aria-label={t('newChat.cardsLabel')}
            aria-hidden={pending || undefined}
            inert={pending || undefined}
        >
            {tasks.map((card) => {
                const Icon = CARD_ICONS[card.skill] ?? ArrowUpRight;
                const { title, note } = cardText(card, t);
                const prompt = card.needs_input ? `${card.prompt.trimEnd()}…` : card.prompt;
                return (
                    <button
                        key={card.skill}
                        type="button"
                        className="try-chip"
                        title={`${note}\n${prompt}`}
                        aria-label={`${title}: ${prompt}`}
                        onClick={() => onPick(card.prompt)}
                        disabled={disabled}
                    >
                        <Icon size={14} aria-hidden="true" />
                        {title}
                    </button>
                );
            })}
            {examples.map(({ icon: Icon, label: labelKey, prompt: promptKey }) => {
                const label = t(labelKey);
                // Plain text for the composer: no English gloss in brackets.
                const prompt = t.bare(promptKey);
                return (
                    <button
                        key={labelKey}
                        type="button"
                        className="try-chip"
                        title={prompt}
                        aria-label={`${label}: ${prompt}`}
                        onClick={() => onPick(prompt)}
                        disabled={disabled}
                    >
                        <Icon size={14} aria-hidden="true" />
                        {label}
                    </button>
                );
            })}
        </div>
    );
}
