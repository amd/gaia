// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The one plain-text form of a guide, shared by copy, share and print. */

import { displayItem } from './quantity';
import { convertTemperatures } from './units';
import type { UnitSystem } from './units';
import type { GuideDoc } from './guideDoc';
import { pluralKey } from '../../../i18n/registry';
import type { Translate } from '../../../i18n/useT';

export interface GuideViewState {
    servings: number | null;
    system: UnitSystem;
    checked: ReadonlySet<number>;
}

export function guideScale(doc: GuideDoc, servings: number | null): number {
    if (!doc.baseServings || !servings) return 1;
    return servings / doc.baseServings;
}

export function itemDisplay(doc: GuideDoc, index: number, state: GuideViewState) {
    const item = doc.items[index];
    return displayItem(item.raw, item.parsed, guideScale(doc, state.servings), state.system);
}

export function stepText(doc: GuideDoc, text: string, system: UnitSystem): string {
    return doc.features.units ? convertTemperatures(text, system) : text;
}

export function servingsLabel(t: Translate, n: number): string {
    return t.plural('cards.servingCount', n);
}

/** A timeline day the model left untitled still reads as its day. */
export function stepTitle(doc: GuideDoc, index: number, dayLabel: (n: number) => string): string | null {
    return doc.steps[index].title ?? (doc.features.timeline ? dayLabel(index + 1) : null);
}

/** Copy, share and print read in one language, so every label is the bare translation. */
export function formatGuideText(doc: GuideDoc, state: GuideViewState, t: Translate): string {
    const out: string[] = [doc.title];
    if (doc.description) out.push(doc.description);
    if (doc.baseServings && state.servings) out.push(t.bare('cards.makes', { servings: t.bare(pluralKey('cards.servingCount', state.servings), { n: state.servings }) }));

    if (doc.items.length) {
        out.push('', t.bare(doc.features.itemsLabel));
        doc.items.forEach((_, i) => {
            const { qty, text } = itemDisplay(doc, i, state);
            const line = qty ? `${qty} ${text}`.trim() : text;
            const mark = doc.features.checkItems ? (state.checked.has(i) ? '[x] ' : '[ ] ') : '- ';
            out.push(`${mark}${line}`);
        });
    }

    if (doc.steps.length && doc.features.stepsLabel) {
        out.push('', t.bare(doc.features.stepsLabel));
        doc.steps.forEach((step, i) => {
            const body = stepText(doc, step.body, state.system);
            const title = stepTitle(doc, i, (n) => t.bare('cards.dayN', { n }));
            const line = title ? `${title}: ${body}` : body;
            out.push(doc.features.timeline ? line : `${i + 1}. ${line}`);
        });
    }

    if (doc.notes) out.push('', t.bare('cards.notes'), stepText(doc, doc.notes, state.system));
    return out.join('\n');
}
