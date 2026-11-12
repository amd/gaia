// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The `guide` card's wire shape (docs/spec/agent-ui-query-sse-contract.md
 * §4.3) and the parsed document every view reads. The model's lines arrive
 * verbatim; all reading of amounts, units and durations happens here.
 */

import { parseQuantity } from './quantity';
import type { ParsedLine } from './quantity';
import { splitTitle } from './durations';
import { temperatureSystem } from './units';
import type { UnitSystem } from './units';

export type GuideType = 'recipe' | 'workout' | 'howto' | 'checklist' | 'itinerary';

export interface GuideWire {
    kind: 'guide';
    guideType: GuideType;
    title: string;
    description?: string | null;
    notes?: string | null;
    servings: number | null;
    items: string[];
    steps: string[];
}

export interface GuideFeatures {
    itemsLabel: string;
    stepsLabel: string | null;
    scaling: boolean;
    units: boolean;
    timers: boolean;
    checkItems: boolean;
    startLabel: string | null;
    /** Steps are days on a timeline, each titled by its "Day N" lead. */
    timeline: boolean;
    /** Shown once, under the first card with hidden controls, until dismissed. */
    tip: string | null;
}

export const GUIDE_FEATURES: Readonly<Record<GuideType, GuideFeatures>> = {
    recipe: {
        itemsLabel: 'Ingredients', stepsLabel: 'Steps', scaling: true, units: true,
        timers: true, checkItems: false, startLabel: 'Start cooking', timeline: false,
        tip: 'Change the servings, switch units, tap a time to start a timer, or press Start cooking for one step at a time.',
    },
    workout: {
        itemsLabel: 'Equipment', stepsLabel: 'Exercises', scaling: false, units: false,
        timers: true, checkItems: true, startLabel: 'Start workout', timeline: false,
        tip: 'Tap a time to start a timer, or press Start workout for one exercise at a time with sets and rest.',
    },
    howto: {
        itemsLabel: 'Materials & tools', stepsLabel: 'Steps', scaling: false, units: true,
        timers: true, checkItems: true, startLabel: 'Start', timeline: false,
        tip: 'Tick off what you have, or press Start for one step at a time.',
    },
    checklist: {
        itemsLabel: 'Items', stepsLabel: null, scaling: false, units: false,
        timers: false, checkItems: true, startLabel: null, timeline: false, tip: null,
    },
    itinerary: {
        itemsLabel: 'Packing', stepsLabel: 'Days', scaling: false, units: false,
        timers: false, checkItems: true, startLabel: null, timeline: true, tip: null,
    },
};

const isString = (v: unknown): v is string => typeof v === 'string';
const isOptionalString = (v: unknown) => v === undefined || v === null || isString(v);
const isLines = (v: unknown): v is string[] => Array.isArray(v) && v.every(isString);

export function isGuideWire(value: unknown): value is GuideWire {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    if (v.kind !== 'guide' || !isString(v.title) || !v.title.trim()) return false;
    if (!(isString(v.guideType) && v.guideType in GUIDE_FEATURES)) return false;
    if (!isOptionalString(v.description) || !isOptionalString(v.notes)) return false;
    if (!isLines(v.items) || !isLines(v.steps)) return false;
    if (v.guideType === 'recipe') {
        return typeof v.servings === 'number' && Number.isInteger(v.servings) && v.servings >= 1;
    }
    return v.servings === null || v.servings === undefined;
}

export interface GuideItem {
    raw: string;
    /** Null for a line with no readable amount; such lines never scale. */
    parsed: ParsedLine | null;
}

export interface GuideStep {
    title: string | null;
    body: string;
}

export interface GuideDoc {
    type: GuideType;
    features: GuideFeatures;
    title: string;
    description: string;
    notes: string;
    baseServings: number | null;
    items: GuideItem[];
    steps: GuideStep[];
    /** The system the guide was written in; the units toggle starts here. */
    nativeSystem: UnitSystem;
    /** Some line has a unit or temperature the toggle would change. */
    convertible: boolean;
    /** Stable per payload, so saved checkmarks follow the guide and nothing else. */
    key: string;
}

/** FNV-1a, as base-36. Only identifies a payload in local storage. */
function hash(text: string): string {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(36);
}

/** The card numbers and bullets lines itself, so a model's own "Step 2:", "3." or "- " goes. */
const LIST_MARKER = /^(?:step\s+\d+\s*[:.)-]|\d+[.)]|[-*•])\s+/i;

export function parseGuideDoc(wire: GuideWire): GuideDoc {
    const features = GUIDE_FEATURES[wire.guideType];
    const lines = (raw: string[]) => raw.map((line) => line.trim().replace(LIST_MARKER, '')).filter(Boolean);
    const items = lines(wire.items).map((raw) => ({
        raw,
        parsed: features.units || features.scaling ? parseQuantity(raw) : null,
    }));
    const steps = lines(wire.steps).map((line) => splitTitle(line, features.timeline));

    const systems: UnitSystem[] = [];
    if (features.units) {
        for (const item of items) if (item.parsed?.unit) systems.push(item.parsed.unit.system);
        for (const step of steps) {
            const found = temperatureSystem(step.body);
            if (found) systems.push(found);
        }
    }
    const metric = systems.filter((s) => s === 'metric').length;
    return {
        type: wire.guideType,
        features,
        title: wire.title.trim(),
        description: (wire.description ?? '').trim(),
        notes: (wire.notes ?? '').trim(),
        baseServings: features.scaling ? wire.servings : null,
        items,
        steps,
        nativeSystem: metric > systems.length - metric ? 'metric' : 'us',
        convertible: systems.length > 0,
        key: hash(JSON.stringify(wire)),
    };
}
