// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The `calculator` card's wire shape (docs/spec/agent-ui-query-sse-contract.md
 * §4.3), one table per calculator, and the math. The model sends only starting
 * numbers; every result is computed here.
 */

export type CalculatorType = 'tip' | 'loan';
export type ValueFormat = 'currency' | 'percent' | 'count' | 'years';

export interface TipInputs {
    bill: number;
    tipPercent: number;
    people: number;
}

export interface LoanInputs {
    amount: number;
    annualRatePercent: number;
    years: number;
    downPayment: number;
}

export type CalculatorWire =
    | { kind: 'calculator'; calculator: 'tip'; title: string; currency: string; inputs: TipInputs }
    | { kind: 'calculator'; calculator: 'loan'; title: string; currency: string; inputs: LoanInputs };

export interface FieldSpec {
    label: string;
    /** The editing floor. The wire's own floors, some of them exclusive, are in `validWire`. */
    min: number;
    max: number;
    step: number;
    format: ValueFormat;
    /** Where the slider stops when it is shorter than `max`; typed values still reach `max`. */
    sliderMax?: number;
    integer?: boolean;
}

export interface ResultLine {
    label: string;
    value: number;
    format: ValueFormat;
    primary?: boolean;
}

export interface CalculatorSpec<I extends object> {
    defaultTitle: string;
    fields: { readonly [K in keyof I]: FieldSpec };
    /** The wire's floors and cross-field rules, beyond every field being 0 to its `max`. */
    validWire: (inputs: I) => boolean;
    results: (inputs: I) => ResultLine[];
}

export function tipResults({ bill, tipPercent, people }: TipInputs) {
    const tip = (bill * tipPercent) / 100;
    const total = bill + tip;
    return { tip, total, perPerson: total / people };
}

/** Standard amortization; a 0% loan is the principal split evenly. */
export function loanResults({ amount, annualRatePercent, years, downPayment }: LoanInputs) {
    const principal = Math.max(0, amount - downPayment);
    const months = Math.round(years * 12);
    const rate = annualRatePercent / 100 / 12;
    const monthly =
        months === 0 ? 0 : rate === 0 ? principal / months : (principal * rate) / (1 - (1 + rate) ** -months);
    const totalPaid = monthly * months;
    return { principal, monthly, totalPaid, interest: totalPaid - principal };
}

const TIP: CalculatorSpec<TipInputs> = {
    defaultTitle: 'Tip calculator',
    fields: {
        bill: { label: 'Bill', min: 0, max: 1e9, step: 0.01, format: 'currency', sliderMax: 500 },
        tipPercent: { label: 'Tip', min: 0, max: 100, step: 1, format: 'percent', sliderMax: 40 },
        people: { label: 'People', min: 1, max: 50, step: 1, format: 'count', sliderMax: 20, integer: true },
    },
    validWire: (inputs) => inputs.bill > 0 && inputs.people >= 1,
    results: (inputs) => {
        const { tip, total, perPerson } = tipResults(inputs);
        const split = inputs.people > 1;
        return [
            { label: 'Tip', value: tip, format: 'currency' },
            { label: 'Total', value: total, format: 'currency', primary: !split },
            ...(split ? [{ label: 'Per person', value: perPerson, format: 'currency' as const, primary: true }] : []),
        ];
    },
};

const LOAN: CalculatorSpec<LoanInputs> = {
    defaultTitle: 'Loan calculator',
    fields: {
        amount: { label: 'Price', min: 0, max: 1e10, step: 1000, format: 'currency', sliderMax: 1_000_000 },
        downPayment: { label: 'Down payment', min: 0, max: 1e10, step: 1000, format: 'currency', sliderMax: 300_000 },
        annualRatePercent: { label: 'Interest rate', min: 0, max: 50, step: 0.05, format: 'percent', sliderMax: 15 },
        years: { label: 'Term', min: 1, max: 50, step: 1, format: 'years' },
    },
    validWire: (inputs) => inputs.amount > 0 && inputs.years > 0 && inputs.downPayment < inputs.amount,
    results: (inputs) => {
        const { principal, monthly, totalPaid, interest } = loanResults(inputs);
        return [
            { label: 'Monthly payment', value: monthly, format: 'currency', primary: true },
            { label: 'Loan amount', value: principal, format: 'currency' },
            { label: 'Total paid', value: totalPaid, format: 'currency' },
            { label: 'Total interest', value: interest, format: 'currency' },
        ];
    },
};

export const CALCULATORS: { readonly tip: CalculatorSpec<TipInputs>; readonly loan: CalculatorSpec<LoanInputs> } = {
    tip: TIP,
    loan: LOAN,
};

/** The one spec shape the card reads; inputs are a flat record of numbers. */
export type AnySpec = CalculatorSpec<Record<string, number>>;

export function specFor(type: CalculatorType): AnySpec {
    return CALCULATORS[type] as unknown as AnySpec;
}

export function isCalculatorWire(value: unknown): value is CalculatorWire {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    if (v.kind !== 'calculator' || (v.calculator !== 'tip' && v.calculator !== 'loan')) return false;
    if (v.title !== undefined && v.title !== null && typeof v.title !== 'string') return false;
    if (typeof v.currency !== 'string' || !isCurrency(v.currency)) return false;
    if (!v.inputs || typeof v.inputs !== 'object') return false;
    const inputs = v.inputs as Record<string, unknown>;
    const spec = specFor(v.calculator);
    for (const [key, field] of Object.entries(spec.fields)) {
        const n = inputs[key];
        if (typeof n !== 'number' || !Number.isFinite(n)) return false;
        if (n < 0 || n > field.max) return false;
        if (field.integer && !Number.isInteger(n)) return false;
    }
    return spec.validWire(inputs as Record<string, number>);
}

/** An ISO code Intl can format, e.g. "EUR". */
function isCurrency(code: string): boolean {
    if (!/^[A-Z]{3}$/.test(code)) return false;
    try {
        new Intl.NumberFormat('en', { style: 'currency', currency: code });
        return true;
    } catch {
        return false;
    }
}

/** How money and numbers read for this viewer: their locale, the card's currency. */
export interface Money {
    locale: string;
    currency: string;
}

export function userLocale(): string {
    return (typeof navigator !== 'undefined' && navigator.language) || 'en-US';
}

export function formatValue(value: number, format: ValueFormat, { locale, currency }: Money): string {
    switch (format) {
        case 'currency':
            return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value);
        case 'percent':
            return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 }).format(value / 100);
        case 'years': {
            const n = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value);
            return `${n} ${value === 1 ? 'year' : 'years'}`;
        }
        case 'count':
            return new Intl.NumberFormat(locale).format(value);
    }
}

export function currencySymbol({ locale, currency }: Money): string {
    const parts = new Intl.NumberFormat(locale, { style: 'currency', currency }).formatToParts(0);
    return parts.find((p) => p.type === 'currency')?.value ?? '';
}

/** The one plain-text form of a calculator, for copy. */
export function formatCalculatorText(
    title: string,
    spec: AnySpec,
    inputs: Record<string, number>,
    money: Money,
): string {
    const out = [title];
    for (const [key, field] of Object.entries(spec.fields)) {
        out.push(`${field.label}: ${formatValue(inputs[key], field.format, money)}`);
    }
    out.push('');
    for (const line of spec.results(inputs)) out.push(`${line.label}: ${formatValue(line.value, line.format, money)}`);
    return out.join('\n');
}
