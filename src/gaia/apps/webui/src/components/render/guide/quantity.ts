// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Reading the amount at the front of an ingredient line, and writing it back
 * scaled and in either unit system. A line with no amount it can read ("a
 * pinch of salt", "Salt to taste") is shown verbatim and never scaled.
 */

import { fractionText, matchUnit, metricText, pickUnit, snapFraction } from './units';
import type { Unit, UnitSystem } from './units';

const GLYPHS: Record<string, number> = {
    '½': 1 / 2, '⅓': 1 / 3, '⅔': 2 / 3, '¼': 1 / 4, '¾': 3 / 4, '⅛': 1 / 8, '⅜': 3 / 8, '⅝': 5 / 8, '⅞': 7 / 8,
    '⅕': 1 / 5, '⅖': 2 / 5, '⅗': 3 / 5, '⅘': 4 / 5, '⅙': 1 / 6, '⅚': 5 / 6,
};
const GLYPH_CLASS = `[${Object.keys(GLYPHS).join('')}]`;

/** One number: "1 1/2", "1½", "1 ½", "1/2", "1.5", "2", "½". */
export const NUMBER_SOURCE =
    String.raw`(?:\d+\s+\d+\/\d+|\d+\s*${GLYPH_CLASS}|\d+\/\d+|\d*\.\d+|\d+|${GLYPH_CLASS})`;

const LEAD = new RegExp(String.raw`^\s*(${NUMBER_SOURCE})(?:\s*(?:-|–|to)\s*(${NUMBER_SOURCE}))?`);

/** The value of a token matched by NUMBER_SOURCE. */
export function parseNumber(token: string): number {
    const t = token.trim();
    const mixed = /^(\d+)\s+(\d+)\/(\d+)$/.exec(t);
    if (mixed) return Number(mixed[1]) + Number(mixed[2]) / Number(mixed[3]);
    const glyph = new RegExp(String.raw`^(\d*)\s*(${GLYPH_CLASS})$`).exec(t);
    if (glyph) return Number(glyph[1] || 0) + GLYPHS[glyph[2]];
    const frac = /^(\d+)\/(\d+)$/.exec(t);
    if (frac) return Number(frac[1]) / Number(frac[2]);
    return Number(t);
}

export interface Amount {
    low: number;
    /** Upper bound of a range ("1-2 cloves"), else null. */
    high: number | null;
}

export interface ParsedLine {
    amount: Amount;
    unit: Unit | null;
    abbreviated: boolean;
    /** The amount and unit exactly as written, for the unscaled view. */
    lead: string;
    /** Everything after the amount and unit: "olive oil", "large eggs, beaten". */
    rest: string;
}

/** "Olive oil (1 tablespoon)": the shape small models write when asked for amount first. */
const TRAILING_AMOUNT = /^([^()]+?)\s*\(([^()]+)\)\s*$/;

function lowerFirst(text: string): string {
    return /^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

export function parseQuantity(line: string): ParsedLine | null {
    const m = LEAD.exec(line);
    if (!m) {
        const trailing = TRAILING_AMOUNT.exec(line);
        const inner = trailing ? parseQuantity(trailing[2]) : null;
        if (!trailing || !inner || inner.rest) return null;
        return { ...inner, rest: lowerFirst(trailing[1].trim()) };
    }
    const low = parseNumber(m[1]);
    const high = m[2] ? parseNumber(m[2]) : null;
    if (!(low > 0) || (high !== null && !(high > low))) return null;
    let end = m[0].length;
    const afterNumber = line.slice(end);
    const gap = /^\s*/.exec(afterNumber)![0].length;
    const unitMatch = matchUnit(afterNumber.slice(gap));
    if (unitMatch) end += gap + unitMatch.length;
    return {
        amount: { low, high },
        unit: unitMatch?.unit ?? null,
        abbreviated: unitMatch?.abbreviated ?? false,
        lead: line.slice(0, end).trim(),
        rest: line.slice(end).trim(),
    };
}

/** Things nobody buys or cooks a fraction of: these scale to whole numbers. */
const INDIVISIBLE = /\b(whole|chickens?|turkeys?|ducks?|eggs?|cans?|tins?|jars?|bottles?|loa(?:f|ves)|packages?|packets?|bags?|boxes|box)\b/i;

/** Count nouns the amount agrees with: "1 egg", "2 eggs". */
const COUNT_NOUNS: ReadonlyArray<readonly [string, string]> = [
    ['egg', 'eggs'], ['clove', 'cloves'], ['can', 'cans'], ['tin', 'tins'], ['jar', 'jars'], ['onion', 'onions'],
    ['lemon', 'lemons'], ['lime', 'limes'], ['potato', 'potatoes'], ['tomato', 'tomatoes'], ['carrot', 'carrots'],
    ['shallot', 'shallots'], ['apple', 'apples'], ['banana', 'bananas'], ['avocado', 'avocados'],
    ['breast', 'breasts'], ['thigh', 'thighs'], ['fillet', 'fillets'], ['slice', 'slices'], ['sprig', 'sprigs'],
    ['stalk', 'stalks'], ['stick', 'sticks'], ['loaf', 'loaves'], ['package', 'packages'], ['packet', 'packets'],
    ['head', 'heads'], ['bunch', 'bunches'], ['leaf', 'leaves'], ['tortilla', 'tortillas'], ['pepper', 'peppers'],
    ['chicken', 'chickens'], ['cucumber', 'cucumbers'], ['yolk', 'yolks'], ['white', 'whites'], ['bag', 'bags'],
];

/** Make the first count noun in `rest` agree with `plural`. Skips "(14 oz)"; stops at a comma or "of". */
function agreeNoun(rest: string, plural: boolean): string {
    const words = rest.split(/(\s+)/);
    let inParen = false;
    for (let i = 0; i < words.length && i < 12; i += 2) {
        const word = words[i];
        if (word.startsWith('(')) inParen = true;
        if (inParen) {
            if (word.includes(')')) inParen = false;
            continue;
        }
        if (/^of$/i.test(word)) break;
        const bare = word.replace(/[,.;:]+$/, '');
        const pair = COUNT_NOUNS.find(([one, many]) => bare.toLowerCase() === one || bare.toLowerCase() === many);
        if (pair) {
            const next = plural ? pair[1] : pair[0];
            const cased = /^[A-Z]/.test(bare) ? next.charAt(0).toUpperCase() + next.slice(1) : next;
            words[i] = cased + word.slice(bare.length);
            return words.join('');
        }
        if (bare !== word) break;
    }
    return rest;
}

function roundCount(n: number, indivisible: boolean): number {
    if (indivisible) return Math.max(1, Math.round(n));
    return Math.max(0.5, Math.round(n * 2) / 2);
}

function amountText(low: number, high: number | null, unit: Unit | null): string {
    const one = (n: number) => (unit?.system === 'metric' ? metricText(n, unit) : fractionText(n));
    const lowText = one(low);
    if (high === null) return lowText;
    const highText = one(high);
    return highText === lowText ? lowText : `${lowText}–${highText}`;
}

function unitName(unit: Unit, abbreviated: boolean, plural: boolean): string {
    if (unit.system === 'metric' || abbreviated) return unit.short;
    return plural ? unit.plural : unit.singular;
}

export interface ItemDisplay {
    /** The amount and unit, or null when the line carries no readable amount. */
    qty: string | null;
    text: string;
}

/**
 * How one ingredient line reads at `scale` in `system`. At scale 1 in the
 * line's own system the line is returned exactly as written.
 */
export function displayItem(raw: string, parsed: ParsedLine | null, scale: number, system: UnitSystem): ItemDisplay {
    if (!parsed) return { qty: null, text: raw };
    const { amount, unit } = parsed;
    const converts = unit !== null && unit.system !== system;
    if (scale === 1 && !converts) return { qty: parsed.lead, text: parsed.rest };

    if (!unit) {
        const indivisible = INDIVISIBLE.test(parsed.rest);
        const low = roundCount(amount.low * scale, indivisible);
        const high = amount.high === null ? null : roundCount(amount.high * scale, indivisible);
        const plural = (high ?? low) > 1;
        return { qty: amountText(low, high, null), text: agreeNoun(parsed.rest, plural) };
    }

    const baseLow = amount.low * scale * unit.toBase;
    const baseHigh = amount.high === null ? null : amount.high * scale * unit.toBase;
    const shown = pickUnit(baseLow, unit, system);
    const low = baseLow / shown.toBase;
    const high = baseHigh === null ? null : baseHigh / shown.toBase;
    const top = high ?? low;
    const plural = (shown.system === 'us' ? snapFraction(top) : top) > 1;
    const abbreviated = !converts && parsed.abbreviated;
    return {
        qty: `${amountText(low, high, shown)} ${unitName(shown, abbreviated, plural)}`,
        text: parsed.rest,
    };
}
