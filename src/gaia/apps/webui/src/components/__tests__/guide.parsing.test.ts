// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { displayItem, parseQuantity } from '../render/guide/quantity';
import { convertTemperatures, fractionText, matchUnit } from '../render/guide/units';
import { findDurations, formatClock, parseSets, splitTitle } from '../render/guide/durations';
import { formatGuideText } from '../render/guide/guideText';
import { isGuideWire, parseGuideDoc } from '../render/guide/guideDoc';
import type { GuideWire } from '../render/guide/guideDoc';

const show = (line: string, scale: number, system: 'us' | 'metric' = 'us') => {
    const { qty, text } = displayItem(line, parseQuantity(line), scale, system);
    return qty ? `${qty} ${text}`.trim() : text;
};

describe('parseQuantity', () => {
    it.each([
        ['1 1/2 cups flour', 1.5, null, 'cup', 'flour'],
        ['1½ cups flour', 1.5, null, 'cup', 'flour'],
        ['1 ½ tsp salt', 1.5, null, 'tsp', 'salt'],
        ['1.5 lb chicken thighs', 1.5, null, 'lb', 'chicken thighs'],
        ['1-2 cloves garlic', 1, 2, null, 'cloves garlic'],
        ['1 to 2 tbsp honey', 1, 2, 'tbsp', 'honey'],
        ['250g butter', 250, null, 'g', 'butter'],
        ['2 large eggs', 2, null, null, 'large eggs'],
        ['4 garlic cloves', 4, null, null, 'garlic cloves'],
        ['3 Tbsp. olive oil', 3, null, 'tbsp', 'olive oil'],
    ])('%s', (line, low, high, unit, rest) => {
        const parsed = parseQuantity(line)!;
        expect(parsed.amount.low).toBeCloseTo(low);
        expect(parsed.amount.high).toBe(high);
        expect(parsed.unit?.id ?? null).toBe(unit);
        expect(parsed.rest).toBe(rest);
    });

    it.each(['a pinch of salt', 'Salt and pepper to taste', 'Fresh parsley, to serve'])('%s has no amount', (line) => {
        expect(parseQuantity(line)).toBeNull();
    });
});

describe('displayItem', () => {
    it.each([
        // Unscaled lines come back exactly as written.
        ['1 1/2 cups flour', 1, 'us', '1 1/2 cups flour'],
        ['a pinch of salt', 3, 'us', 'a pinch of salt'],
        // Friendly fractions, never raw decimals.
        ['1 cup flour', 1 / 3, 'us', '⅓ cup flour'],
        ['2 cups stock', 0.75, 'us', '1½ cups stock'],
        // Units agree with the amount.
        ['2 teaspoons salt', 0.5, 'us', '1 teaspoon salt'],
        ['1 teaspoon salt', 0.5, 'us', '½ teaspoon salt'],
        ['1 teaspoon salt', 2, 'us', '2 teaspoons salt'],
        // Spoons move up when the larger unit lands cleanly.
        ['6 tbsp butter', 2, 'us', '¾ cup butter'],
        ['2 tsp cumin', 1.5, 'us', '1 tbsp cumin'],
        // Indivisible things round to whole, never below one.
        ['1 whole chicken', 0.3, 'us', '1 whole chicken'],
        ['2 eggs', 0.25, 'us', '1 egg'],
        ['3 eggs', 2, 'us', '6 eggs'],
        ['1 (14 oz) can tomatoes', 2, 'us', '2 (14 oz) cans tomatoes'],
        // Other counts round to the half.
        ['1 onion, diced', 1.5, 'us', '1½ onions, diced'],
        ['1-2 cloves garlic', 2, 'us', '2–4 cloves garlic'],
        // Metric rounds to whole numbers, to 5 above 20.
        ['250 g butter', 1 / 3, 'metric', '85 g butter'],
        ['5 ml vanilla', 0.5, 'metric', '3 ml vanilla'],
        ['800 g flour', 1.5, 'metric', '1.2 kg flour'],
        // Conversion is volume to volume and mass to mass.
        ['1 cup milk', 1, 'metric', '235 ml milk'],
        ['1 lb ground beef', 1, 'metric', '455 g ground beef'],
        ['250 ml cream', 1, 'us', '1 cup cream'],
        ['500 g pasta', 1, 'us', '1⅛ pounds pasta'],
    ] as const)('%s ×%s %s', (line, scale, system, expected) => {
        expect(show(line, scale, system)).toBe(expected);
    });
});

describe('units', () => {
    it.each([
        [1 / 3, '⅓'], [0.33, '⅓'], [1.5, '1½'], [2, '2'], [0.05, '⅛'], [0.7, '⅔'], [2.9, '3'],
    ])('fractionText(%s) is %s', (n, text) => {
        expect(fractionText(n)).toBe(text);
    });

    it.each([
        ['tablespoons', 'tbsp', false], ['tbsp', 'tbsp', true], ['oz', 'oz', true], ['large', null, false], ['grated', null, false],
    ])('matchUnit(%s)', (text, id, abbreviated) => {
        const m = matchUnit(text);
        expect(m?.unit.id ?? null).toBe(id);
        if (m) expect(m.abbreviated).toBe(abbreviated);
    });

    it.each([
        ['Heat the oven to 425°F.', 'metric', 'Heat the oven to 220°C.'],
        ['Bake at 350 degrees F until set', 'metric', 'Bake at 175°C until set'],
        ['Preheat to 200°C', 'us', 'Preheat to 390°F'],
        ['Preheat to 200°C', 'metric', 'Preheat to 200°C'],
        ['Add 3 C of water', 'metric', 'Add 3 C of water'],
    ] as const)('%s in %s', (text, system, expected) => {
        expect(convertTemperatures(text, system)).toBe(expected);
    });
});

describe('durations', () => {
    const found = (text: string) => findDurations(text).map((d) => [text.slice(d.start, d.end), d.seconds]);

    it.each([
        ['Simmer 15 minutes.', [['15 minutes', 900]]],
        ['Roast 1 hr 20 min, then rest.', [['1 hr 20 min', 4800]]],
        ['Roast 1 hour and 20 minutes', [['1 hour and 20 minutes', 4800]]],
        ['Cook 10-15 min until golden', [['10-15 min', 600]]],
        ['Hold 45 seconds', [['45 seconds', 45]]],
        ['Chill for an hour', [['an hour', 3600]]],
        ['Bake 1½ hours', [['1½ hours', 5400]]],
        ['A 2-minute plank', [['2-minute', 120]]],
        ['Sear 3 minutes per side, rest 10 minutes', [['3 minutes', 180], ['10 minutes', 600]]],
        ['Cook a second batch for a few minutes', []],
    ])('%s', (text, expected) => {
        expect(found(text)).toEqual(expected);
    });

    it.each([[900, '15:00'], [45, '0:45'], [4800, '1:20:00'], [0.2, '0:01']])('formatClock(%s)', (s, text) => {
        expect(formatClock(s)).toBe(text);
    });

    it.each([
        ['Sear the bird: Pat it dry and brown it.', 'Sear the bird', 'Pat it dry and brown it.'],
        ['Push-ups: 3 sets of 12', 'Push-ups', '3 sets of 12'],
        ['Once the onions are soft and golden: add the garlic', null,
            'Once the onions are soft and golden: add the garlic'],
        ['Whisk the eggs.', null, 'Whisk the eggs.'],
    ])('splitTitle(%s)', (step, title, body) => {
        expect(splitTitle(step)).toEqual({ title, body });
    });

    it.each([
        ['3 sets of 12, rest 45 seconds', { sets: 3, reps: 12, holdIndex: null, restIndex: 0 }],
        ['3 x 12', { sets: 3, reps: 12, holdIndex: null, restIndex: null }],
        ['Plank: hold 45 seconds, 3 sets, rest 30 seconds', { sets: 3, reps: null, holdIndex: 0, restIndex: 1 }],
    ])('parseSets(%s)', (text, expected) => {
        expect(parseSets(text)).toEqual(expected);
    });
});

const recipe: GuideWire = {
    kind: 'guide',
    guideType: 'recipe',
    title: 'Roast chicken',
    description: 'Crisp skin, juicy meat.',
    notes: 'Rest before carving.',
    servings: 4,
    items: ['1 whole chicken', '2 tablespoons olive oil', 'Salt to taste'],
    steps: ['Heat the oven: Set it to 425°F.', 'Roast 1 hr 20 min.'],
};

describe('guide document', () => {
    it('rejects payloads that break the wire shape', () => {
        expect(isGuideWire(recipe)).toBe(true);
        expect(isGuideWire({ ...recipe, guideType: 'poem' })).toBe(false);
        expect(isGuideWire({ ...recipe, servings: null })).toBe(false);
        expect(isGuideWire({ ...recipe, items: [1] })).toBe(false);
        expect(isGuideWire({ ...recipe, title: '' })).toBe(false);
        expect(isGuideWire({ ...recipe, kind: 'list' })).toBe(false);
    });

    it('formats one text for copy, share and print', () => {
        const doc = parseGuideDoc(recipe);
        expect(formatGuideText(doc, { servings: 2, system: 'metric', checked: new Set() })).toBe([
            'Roast chicken',
            'Crisp skin, juicy meat.',
            'Makes 2 servings',
            '',
            'Ingredients',
            '- 1 whole chicken',
            '- 15 ml olive oil',
            '- Salt to taste',
            '',
            'Steps',
            '1. Heat the oven: Set it to 220°C.',
            '2. Roast 1 hr 20 min.',
            '',
            'Notes',
            'Rest before carving.',
        ].join('\n'));
    });

    it('marks checked items on checkable guides', () => {
        const doc = parseGuideDoc({
            kind: 'guide', guideType: 'checklist', title: 'Packing', servings: null,
            items: ['Passport', 'Charger'], steps: [],
        });
        expect(formatGuideText(doc, { servings: null, system: 'us', checked: new Set([1]) }))
            .toBe('Packing\n\nItems\n[ ] Passport\n[x] Charger');
    });
});
