// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Calculator, ChefHat, Dumbbell, ListChecks, Map, Receipt, Wrench } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import './StartCards.css';

interface TryExample {
    icon: LucideIcon;
    label: string;
    prompt: string;
}

/** One example per card the flagship can draw, so each chip ends in a card. */
export const TRY_EXAMPLES: readonly TryExample[] = [
    { icon: ChefHat, label: 'Recipe', prompt: 'Give me a banana bread recipe' },
    { icon: Dumbbell, label: 'Workout', prompt: 'A 15-minute workout I can do at home' },
    { icon: Wrench, label: 'How-to', prompt: 'How do I fix a running toilet?' },
    { icon: ListChecks, label: 'Checklist', prompt: 'Packing list for a beach weekend' },
    { icon: Map, label: 'Trip plan', prompt: 'Plan 3 days in Lisbon' },
    { icon: Receipt, label: 'Split a bill', prompt: 'Split a $140 dinner four ways with a 20% tip' },
    { icon: Calculator, label: 'Loan payment', prompt: 'Monthly payment on a $300,000 house at 6% for 30 years' },
];

/** Everyday asks that come back as interactive cards; a pick fills the box to edit or send. */
export function TryChips({ onPick, disabled }: { onPick: (prompt: string) => void; disabled?: boolean }) {
    return (
        <div className="try-chips" role="group" aria-label="Try an example">
            <span className="try-chips-label" aria-hidden="true">Try</span>
            {TRY_EXAMPLES.map(({ icon: Icon, label, prompt }) => (
                <button
                    key={label}
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
            ))}
        </div>
    );
}
