// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RenderCard } from '../render/RenderCard';
import {
    currencyFor,
    formatCalculatorText,
    formatValue,
    isCalculatorWire,
    loanResults,
    specFor,
    tipResults,
} from '../render/calculator/calculatorDoc';

const loan = {
    kind: 'calculator',
    calculator: 'loan',
    title: '',
    inputs: { amount: 250000, annualRatePercent: 6, years: 30, downPayment: 50000 },
};

const tip = {
    kind: 'calculator',
    calculator: 'tip',
    title: 'Dinner',
    inputs: { bill: 84.5, tipPercent: 18, people: 3 },
};

const cents = (n: number) => Math.round(n * 100) / 100;

describe('calculator math', () => {
    it.each([
        [{ amount: 200000, annualRatePercent: 6, years: 30, downPayment: 0 }, 1199.1, 431676.38, 231676.38],
        [{ amount: 250000, annualRatePercent: 6, years: 30, downPayment: 50000 }, 1199.1, 431676.38, 231676.38],
        [{ amount: 30000, annualRatePercent: 7.5, years: 5, downPayment: 0 }, 601.14, 36068.31, 6068.31],
        [{ amount: 12000, annualRatePercent: 0, years: 2, downPayment: 0 }, 500, 12000, 0],
    ])('loanResults(%o)', (inputs, monthly, totalPaid, interest) => {
        const r = loanResults(inputs);
        expect(cents(r.monthly)).toBe(monthly);
        expect(cents(r.totalPaid)).toBe(totalPaid);
        expect(cents(r.interest)).toBe(interest);
    });

    it('treats a down payment at or past the price as nothing borrowed', () => {
        expect(loanResults({ amount: 100, annualRatePercent: 5, years: 1, downPayment: 150 })).toEqual({
            principal: 0, monthly: 0, totalPaid: 0, interest: 0,
        });
    });

    it.each([
        [{ bill: 84.5, tipPercent: 18, people: 3 }, 15.21, 99.71, 33.24],
        [{ bill: 100, tipPercent: 0, people: 1 }, 0, 100, 100],
        [{ bill: 60, tipPercent: 20, people: 4 }, 12, 72, 18],
    ])('tipResults(%o)', (inputs, tipAmount, total, perPerson) => {
        const r = tipResults(inputs);
        expect(cents(r.tip)).toBe(tipAmount);
        expect(cents(r.total)).toBe(total);
        expect(cents(r.perPerson)).toBe(perPerson);
    });

    it('formats money in the locale\'s own currency', () => {
        expect(formatValue(1199.1, 'currency', 'en-US')).toBe('$1,199.10');
        expect(currencyFor('de-DE')).toBe('EUR');
        expect(currencyFor('en-GB')).toBe('GBP');
        expect(formatValue(6.5, 'percent', 'en-US')).toBe('6.5%');
        expect(formatValue(1, 'years', 'en-US')).toBe('1 year');
    });

    it('formats one plain text for copy', () => {
        expect(formatCalculatorText('Dinner', specFor('tip'), tip.inputs, 'en-US')).toBe([
            'Dinner',
            'Bill: $84.50',
            'Tip: 18%',
            'People: 3',
            '',
            'Tip: $15.21',
            'Total: $99.71',
            'Per person: $33.24',
        ].join('\n'));
    });
});

describe('calculator wire', () => {
    it.each([
        ['a loan', loan, true],
        ['a tip', tip, true],
        ['an unknown calculator', { ...tip, calculator: 'bmi' }, false],
        ['a wrong kind', { ...tip, kind: 'guide' }, false],
        ['a string amount', { ...loan, inputs: { ...loan.inputs, amount: '250000' } }, false],
        ['a missing input', { ...tip, inputs: { bill: 10, tipPercent: 18 } }, false],
        ['a zero bill', { ...tip, inputs: { ...tip.inputs, bill: 0 } }, false],
        ['half a person', { ...tip, inputs: { ...tip.inputs, people: 1.5 } }, false],
        ['a tip over 100%', { ...tip, inputs: { ...tip.inputs, tipPercent: 120 } }, false],
        ['a down payment at the price', { ...loan, inputs: { ...loan.inputs, downPayment: 250000 } }, false],
        ['a zero term', { ...loan, inputs: { ...loan.inputs, years: 0 } }, false],
        ['a three-month term', { ...loan, inputs: { ...loan.inputs, years: 0.25 } }, true],
    ])('%s', (_name, payload, ok) => {
        expect(isCalculatorWire(payload)).toBe(ok);
    });
});

describe('CalculatorCard', () => {
    it('recomputes the monthly payment as an input changes', () => {
        render(<RenderCard render="calculator" data={loan} />);
        expect(screen.getByRole('heading', { name: 'Loan calculator' })).toBeInTheDocument();
        expect(screen.getByText('$1,199.10')).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText('Down payment'), { target: { value: '0' } });
        expect(screen.getByText('$1,498.88')).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText('Interest rate slider'), { target: { value: '0' } });
        expect(screen.getByText('$694.44')).toBeInTheDocument();
    });

    it('splits the bill and keeps a half-typed value without breaking the math', () => {
        render(<RenderCard render="calculator" data={tip} />);
        expect(screen.getByText('$33.24')).toBeInTheDocument();

        const people = screen.getByLabelText('People');
        fireEvent.change(people, { target: { value: '' } });
        expect(people).toHaveValue(null);
        expect(screen.getByText('$33.24')).toBeInTheDocument();

        fireEvent.change(people, { target: { value: '2' } });
        expect(screen.getByText('$49.86')).toBeInTheDocument();
    });

    it('shows the unsupported card for a payload that breaks the shape', () => {
        render(<RenderCard render="calculator" data={{ ...tip, inputs: { bill: 'lots' } }} />);
        expect(screen.getByText('Invalid calculator payload')).toBeInTheDocument();
    });
});
