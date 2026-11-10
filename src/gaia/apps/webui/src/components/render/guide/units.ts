// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Measurement units a guide line can carry, and conversion between US and
 * metric. Volume converts to volume and mass to mass only: turning "1 cup
 * flour" into grams needs a density per ingredient, and a guess is worse
 * than leaving the line alone.
 */

export type UnitSystem = 'us' | 'metric';
export type Dimension = 'volume' | 'mass';

export interface Unit {
    id: string;
    dimension: Dimension;
    system: UnitSystem;
    /** Millilitres (volume) or grams (mass) in one of this unit. */
    toBase: number;
    singular: string;
    plural: string;
    /** Shown when the source line abbreviated the unit; metric is always short. */
    short: string;
    /** Lower-case spellings a line may use. */
    aliases: string[];
}

function unit(
    id: string, dimension: Dimension, system: UnitSystem, toBase: number,
    singular: string, plural: string, short: string, aliases: string[],
): Unit {
    return { id, dimension, system, toBase, singular, plural, short, aliases };
}

export const UNITS: readonly Unit[] = [
    unit('tsp', 'volume', 'us', 4.92892, 'teaspoon', 'teaspoons', 'tsp', ['teaspoons', 'teaspoon', 'tsps', 'tsp']),
    unit('tbsp', 'volume', 'us', 14.7868, 'tablespoon', 'tablespoons', 'tbsp', ['tablespoons', 'tablespoon', 'tbsps', 'tbsp', 'tbs', 'tbl']),
    unit('floz', 'volume', 'us', 29.5735, 'fluid ounce', 'fluid ounces', 'fl oz', ['fluid ounces', 'fluid ounce', 'fl. oz', 'fl oz']),
    unit('cup', 'volume', 'us', 236.588, 'cup', 'cups', 'cup', ['cups', 'cup']),
    unit('pint', 'volume', 'us', 473.176, 'pint', 'pints', 'pt', ['pints', 'pint', 'pt']),
    unit('quart', 'volume', 'us', 946.353, 'quart', 'quarts', 'qt', ['quarts', 'quart', 'qt']),
    unit('gallon', 'volume', 'us', 3785.41, 'gallon', 'gallons', 'gal', ['gallons', 'gallon', 'gal']),
    unit('oz', 'mass', 'us', 28.3495, 'ounce', 'ounces', 'oz', ['ounces', 'ounce', 'oz']),
    unit('lb', 'mass', 'us', 453.592, 'pound', 'pounds', 'lb', ['pounds', 'pound', 'lbs', 'lb']),
    unit('ml', 'volume', 'metric', 1, 'ml', 'ml', 'ml', ['millilitres', 'milliliters', 'millilitre', 'milliliter', 'ml']),
    unit('l', 'volume', 'metric', 1000, 'L', 'L', 'L', ['litres', 'liters', 'litre', 'liter', 'l']),
    unit('g', 'mass', 'metric', 1, 'g', 'g', 'g', ['grams', 'gram', 'g']),
    unit('kg', 'mass', 'metric', 1000, 'kg', 'kg', 'kg', ['kilograms', 'kilogram', 'kilos', 'kilo', 'kg']),
];

export const UNIT_BY_ID: Readonly<Record<string, Unit>> = Object.fromEntries(UNITS.map((u) => [u.id, u]));

export interface UnitMatch {
    unit: Unit;
    /** The line spelled the unit as an abbreviation ("tbsp", "oz"). */
    abbreviated: boolean;
    /** Characters consumed from the input, trailing period included. */
    length: number;
}

const ALIASES = UNITS
    .flatMap((u) => u.aliases.map((alias) => ({ alias, unit: u })))
    .sort((a, b) => b.alias.length - a.alias.length);

/** The unit spelled at the start of `text`, if any. A unit must end at a word boundary. */
export function matchUnit(text: string): UnitMatch | null {
    const lower = text.toLowerCase();
    for (const { alias, unit: u } of ALIASES) {
        if (!lower.startsWith(alias)) continue;
        const next = lower.charAt(alias.length);
        if (/[a-z0-9]/.test(next)) continue;
        const length = alias.length + (next === '.' ? 1 : 0);
        const abbreviated = alias !== u.singular.toLowerCase() && alias !== u.plural.toLowerCase();
        return { unit: u, abbreviated, length };
    }
    return null;
}

/** Ascending, the units a converted or rescaled amount may be shown in. */
const CHAINS: Record<UnitSystem, Record<Dimension, string[]>> = {
    us: { volume: ['tsp', 'tbsp', 'cup'], mass: ['oz', 'lb'] },
    metric: { volume: ['ml', 'l'], mass: ['g', 'kg'] },
};

/** Smallest amount a larger chain step is used for, in that unit. */
const MIN_AMOUNT: Record<string, number> = { tbsp: 1, cup: 0.25, lb: 1, l: 1, kg: 1 };

/**
 * Spoons and cups only move up when the larger unit lands on a clean
 * fraction. Converting from metric is approximate anyway, so it allows more.
 */
const SNAP_TOLERANCE = 0.04;
const CONVERT_TOLERANCE = 0.1;

/**
 * The unit to show `base` (ml or g) in. `original` stays when it is in the
 * target system and outside that system's chain, so a pint stays a pint.
 */
export function pickUnit(base: number, original: Unit, system: UnitSystem): Unit {
    const chain = CHAINS[system][original.dimension];
    if (original.system === system && !chain.includes(original.id)) return original;
    const tolerance = original.system === system ? SNAP_TOLERANCE : CONVERT_TOLERANCE;
    for (let i = chain.length - 1; i > 0; i--) {
        const candidate = UNIT_BY_ID[chain[i]];
        const amount = base / candidate.toBase;
        if (amount < MIN_AMOUNT[candidate.id] * (1 - tolerance)) continue;
        if (candidate.system === 'us' && candidate.dimension === 'volume') {
            if (Math.abs(snapFraction(amount) - amount) / amount > tolerance) continue;
        }
        return candidate;
    }
    return UNIT_BY_ID[chain[0]];
}

/** Fractional parts a US amount rounds to, with their glyphs. */
const FRACTIONS: ReadonlyArray<readonly [number, string]> = [
    [0, ''], [1 / 8, '⅛'], [1 / 4, '¼'], [1 / 3, '⅓'], [1 / 2, '½'], [2 / 3, '⅔'], [3 / 4, '¾'], [1, ''],
];

/** `n` rounded to a whole plus a friendly fraction, never below ⅛. */
export function snapFraction(n: number): number {
    const whole = Math.floor(n);
    const frac = n - whole;
    let best = 0;
    for (const [value] of FRACTIONS) if (Math.abs(value - frac) < Math.abs(best - frac)) best = value;
    return Math.max(whole + best, 1 / 8);
}

/** "1½", "⅓", "12": a snapped amount written with a fraction glyph, never a decimal. */
export function fractionText(n: number): string {
    const snapped = snapFraction(n);
    const whole = Math.floor(snapped + 1e-9);
    const frac = snapped - whole;
    const glyph = FRACTIONS.find(([value]) => Math.abs(value - frac) < 1e-6)?.[1] ?? '';
    if (whole === 0) return glyph;
    return `${whole}${glyph}`;
}

/** Metric amounts: whole numbers, to 5 from 20 up; litres and kilos to one decimal. */
export function metricText(n: number, u: Unit): string {
    if (u.id === 'l' || u.id === 'kg') return String(Math.round(n * 10) / 10);
    if (n >= 20) return String(Math.round(n / 5) * 5);
    return String(Math.max(1, Math.round(n)));
}

const TEMPERATURE = /(\d{2,3})(?:\s*°\s*|\s*degrees?\s+|(?=[FC]\b))(Fahrenheit|Celsius|F|C)\b/g;

/** Oven settings round to 5 degrees; doneness temperatures (74°C, 165°F) stay exact. */
const roundDegrees = (n: number) => (n >= 150 ? Math.round(n / 5) * 5 : Math.round(n));

const PAIR_GAP = /^\s*(?:\/|\(|or)\s*$/;

/**
 * Rewrite temperatures in `text` into `system`. A pair the author already wrote
 * in both systems ("165°F/74°C", "425°F (220°C)") keeps only their own value
 * for `system`, instead of converting each half into a duplicate.
 */
export function convertTemperatures(text: string, system: UnitSystem): string {
    const found = [...text.matchAll(TEMPERATURE)].map((m) => ({
        start: m.index!,
        end: m.index! + m[0].length,
        value: Number(m[1]),
        fahrenheit: m[2].charAt(0) === 'F',
        text: m[0],
    }));
    let out = '';
    let at = 0;
    for (let i = 0; i < found.length; i++) {
        const t = found[i];
        const next = found[i + 1];
        out += text.slice(at, t.start);
        if (next && next.fahrenheit !== t.fahrenheit && PAIR_GAP.test(text.slice(t.end, next.start))) {
            const wanted = (t.fahrenheit ? system === 'us' : system === 'metric') ? t : next;
            out += wanted.text;
            at = next.end;
            if (text.slice(t.end, next.start).includes('(') && text.charAt(at) === ')') at += 1;
            i += 1;
            continue;
        }
        if (t.fahrenheit && system === 'metric') out += `${roundDegrees(((t.value - 32) * 5) / 9)}°C`;
        else if (!t.fahrenheit && system === 'us') out += `${roundDegrees((t.value * 9) / 5 + 32)}°F`;
        else out += t.text;
        at = t.end;
    }
    return out + text.slice(at);
}

/** The unit system of the first temperature in `text`, if it names one. */
export function temperatureSystem(text: string): UnitSystem | null {
    const m = new RegExp(TEMPERATURE.source).exec(text);
    if (!m) return null;
    return m[2].charAt(0) === 'F' ? 'us' : 'metric';
}
