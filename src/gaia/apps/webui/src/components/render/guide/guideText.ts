// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The one plain-text form of a guide, shared by copy, share and print. */

import { displayItem } from './quantity';
import { convertTemperatures } from './units';
import type { UnitSystem } from './units';
import type { GuideDoc } from './guideDoc';

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

export function servingsLabel(n: number): string {
    return `${n} ${n === 1 ? 'serving' : 'servings'}`;
}

export function formatGuideText(doc: GuideDoc, state: GuideViewState): string {
    const out: string[] = [doc.title];
    if (doc.description) out.push(doc.description);
    if (doc.baseServings && state.servings) out.push(`Makes ${servingsLabel(state.servings)}`);

    if (doc.items.length) {
        out.push('', doc.features.itemsLabel);
        doc.items.forEach((_, i) => {
            const { qty, text } = itemDisplay(doc, i, state);
            const line = qty ? `${qty} ${text}`.trim() : text;
            const mark = doc.features.checkItems ? (state.checked.has(i) ? '[x] ' : '[ ] ') : '- ';
            out.push(`${mark}${line}`);
        });
    }

    if (doc.steps.length && doc.features.stepsLabel) {
        out.push('', doc.features.stepsLabel);
        doc.steps.forEach((step, i) => {
            const body = stepText(doc, step.body, state.system);
            out.push(`${i + 1}. ${step.title ? `${step.title}: ${body}` : body}`);
        });
    }

    if (doc.notes) out.push('', 'Notes', stepText(doc, doc.notes, state.system));
    return out.join('\n');
}
