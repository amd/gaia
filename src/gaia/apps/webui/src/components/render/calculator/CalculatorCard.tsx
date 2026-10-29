// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { UnsupportedCard } from '../UnsupportedCard';
import { copyGuide } from '../guide/guideActions';
import {
    currencySymbol,
    formatCalculatorText,
    formatValue,
    isCalculatorWire,
    specFor,
    userLocale,
} from './calculatorDoc';
import type { CalculatorWire, FieldSpec } from './calculatorDoc';
import '../guide/guide.css';
import './calculator.css';

const NOTICE_MS = 2500;

export function CalculatorCard({ data }: { data: unknown }) {
    if (!isCalculatorWire(data)) return <UnsupportedCard variant="invalid" render="calculator" data={data} />;
    return <Calculator wire={data} />;
}

function Calculator({ wire }: { wire: CalculatorWire }) {
    const spec = specFor(wire.calculator);
    const locale = useMemo(userLocale, []);
    const [values, setValues] = useState<Record<string, number>>(() => ({ ...wire.inputs }));
    const [notice, setNotice] = useState<string | null>(null);
    const title = wire.title?.trim() || spec.defaultTitle;
    const results = spec.results(values);

    useEffect(() => {
        if (!notice) return;
        const id = setTimeout(() => setNotice(null), NOTICE_MS);
        return () => clearTimeout(id);
    }, [notice]);

    const onCopy = () => {
        copyGuide(formatCalculatorText(title, spec, values, locale)).then(
            () => setNotice('Copied'),
            (err: unknown) => setNotice(`Couldn't copy: ${err instanceof Error ? err.message : String(err)}`),
        );
    };

    return (
        <article className="render-guide render-calc" aria-label={title}>
            <header className="render-calc__header">
                <h3 className="render-guide__title">{title}</h3>
                <button type="button" className="render-guide__icon" onClick={onCopy} aria-label="Copy" title="Copy">
                    {notice === 'Copied' ? <Check size={15} /> : <Copy size={15} />}
                </button>
            </header>
            <p className="render-guide__notice" role="status">{notice}</p>

            <div className="render-calc__fields">
                {Object.entries(spec.fields).map(([key, field]) => (
                    <NumberField
                        key={key}
                        field={field}
                        value={values[key]}
                        locale={locale}
                        onChange={(n) => setValues((prev) => ({ ...prev, [key]: n }))}
                    />
                ))}
            </div>

            <dl className="render-calc__results" aria-live="polite">
                {results.map((line) => (
                    <div key={line.label} className={`render-calc__result${line.primary ? ' is-primary' : ''}`}>
                        <dt>{line.label}</dt>
                        <dd>{formatValue(line.value, line.format, locale)}</dd>
                    </div>
                ))}
            </dl>
        </article>
    );
}

interface NumberFieldProps {
    field: FieldSpec;
    value: number;
    locale: string;
    onChange: (value: number) => void;
}

/** A typed box and a slider on one value. The box keeps what is typed until it reads as a number in range. */
function NumberField({ field, value, locale, onChange }: NumberFieldProps) {
    const [draft, setDraft] = useState<string | null>(null);
    const sliderMax = Math.min(field.max, Math.max(field.sliderMax ?? field.max, value));
    const prefix = field.format === 'currency' ? currencySymbol(locale) : '';
    const suffix = field.format === 'percent' ? '%' : field.format === 'years' ? 'yrs' : '';

    const onType = (text: string) => {
        setDraft(text);
        const n = Number(text);
        if (text.trim() === '' || !Number.isFinite(n) || n < field.min || n > field.max) return;
        if (field.integer && !Number.isInteger(n)) return;
        onChange(n);
    };

    return (
        <div className="render-calc__field">
            <label className="render-calc__label">
                <span>{field.label}</span>
                <span className="render-calc__box">
                    {prefix && <span className="render-calc__affix">{prefix}</span>}
                    <input
                        type="number"
                        inputMode="decimal"
                        min={field.min}
                        max={field.max}
                        step={field.step}
                        value={draft ?? String(value)}
                        aria-label={field.label}
                        onChange={(e) => onType(e.target.value)}
                        onBlur={() => setDraft(null)}
                    />
                    {suffix && <span className="render-calc__affix">{suffix}</span>}
                </span>
            </label>
            <input
                type="range"
                className="render-calc__slider"
                min={field.min}
                max={sliderMax}
                step={field.step}
                value={value}
                aria-label={`${field.label} slider`}
                aria-valuetext={formatValue(value, field.format, locale)}
                onChange={(e) => {
                    setDraft(null);
                    onChange(Number(e.target.value));
                }}
            />
        </div>
    );
}
