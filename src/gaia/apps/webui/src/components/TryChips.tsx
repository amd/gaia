// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Calculator, ChefHat, Dumbbell, ListChecks, Map, Receipt, Wrench } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import './StartCards.css';
import { useT } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';

interface TryExample {
    icon: LucideIcon;
    label: MessageKey;
    prompt: MessageKey;
}

/** One example per card the flagship can draw, so each chip ends in a card. */
export const TRY_EXAMPLES: readonly TryExample[] = [
    { icon: ChefHat, label: 'cards.try.recipe', prompt: 'cards.try.recipePrompt' },
    { icon: Dumbbell, label: 'cards.try.workout', prompt: 'cards.try.workoutPrompt' },
    { icon: Wrench, label: 'cards.try.howTo', prompt: 'cards.try.howToPrompt' },
    { icon: ListChecks, label: 'cards.try.checklist', prompt: 'cards.try.checklistPrompt' },
    { icon: Map, label: 'cards.try.trip', prompt: 'cards.try.tripPrompt' },
    { icon: Receipt, label: 'cards.try.bill', prompt: 'cards.try.billPrompt' },
    { icon: Calculator, label: 'cards.try.loan', prompt: 'cards.try.loanPrompt' },
];

/** Everyday asks that come back as interactive cards; a pick fills the box to edit or send. */
export function TryChips({ onPick, disabled }: { onPick: (prompt: string) => void; disabled?: boolean }) {
    const t = useT();
    return (
        <div className="try-chips" role="group" aria-label={t('cards.tryGroup')}>
            <span className="try-chips-label" aria-hidden="true">{t('cards.try')}</span>
            {TRY_EXAMPLES.map(({ icon: Icon, label: labelKey, prompt: promptKey }) => {
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
