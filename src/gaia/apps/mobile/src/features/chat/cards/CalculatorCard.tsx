// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';

import {
    currencySymbol,
    formatCalculatorText,
    formatValue,
    isCalculatorWire,
    specFor,
    type CalculatorWire,
    type FieldSpec,
    type Money,
} from '@webui/components/render/calculator/calculatorDoc';

import { useT, type Translate } from '@/i18n/useT';
import { IconButton, Ltr, Row, Slider, Stack, Surface, Text, TextField } from '@/ui';
import { Check, Copy } from '@/ui/icons';

import { copyText, errorText } from './guideActions';
import { UnsupportedCard } from './UnsupportedCard';

const NOTICE_MS = 2500;

// Latin digits, so what the person types and what the card shows match.
const moneyLocale = (t: Translate): string => (t.locale === 'en' ? 'en-US' : `${t.locale}-u-nu-latn`);

export function CalculatorCard({ data }: { data: unknown }) {
    if (!isCalculatorWire(data)) return <UnsupportedCard variant="invalid" render="calculator" data={data} />;
    return <Calculator wire={data} />;
}

function Calculator({ wire }: { wire: CalculatorWire }) {
    const t = useT();
    const spec = specFor(wire.calculator);
    const locale = moneyLocale(t);
    const money = useMemo(() => ({ locale, currency: wire.currency }), [locale, wire.currency]);
    const [values, setValues] = useState<Record<string, number>>(() => ({ ...wire.inputs }));
    const [notice, setNotice] = useState<string | null>(null);
    const title = wire.title?.trim() || t(spec.defaultTitle);
    const results = spec.results(values);
    const copiedNotice = t('cards.copied');

    useEffect(() => {
        if (!notice) return;
        const id = setTimeout(() => setNotice(null), NOTICE_MS);
        return () => clearTimeout(id);
    }, [notice]);

    const onCopy = () =>
        copyText(formatCalculatorText(title, spec, values, money, t)).then(
            () => setNotice(copiedNotice),
            (err: unknown) => setNotice(t('cards.copyFailed', { error: errorText(err) })),
        );

    return (
        <Surface tone="card" radius="md" border padding="md" testID={`card-calculator-${wire.calculator}`}>
            <Stack gap="md">
                <Row gap="sm">
                    <Stack flex={1}>
                        <Text role="title" weight="semibold" dir="auto" accessibilityRole="header">
                            {title}
                        </Text>
                    </Stack>
                    <IconButton icon={notice === copiedNotice ? Check : Copy} label={t('cards.copy')} size="sm" onPress={onCopy} testID="calc-copy" />
                </Row>
                {notice ? (
                    <Text role="caption" tone="secondary" dir="auto" accessibilityRole="alert">
                        {notice}
                    </Text>
                ) : null}

                {Object.entries(spec.fields).map(([key, field]) => (
                    <NumberField key={key} id={key} field={field} value={values[key]} money={money} onChange={(n) => setValues((prev) => ({ ...prev, [key]: n }))} />
                ))}

                <Stack gap="xs" testID="calc-results">
                    {results.map((line) => (
                        <Row key={line.label} gap="sm">
                            <Stack flex={1}>
                                <Text role={line.primary ? 'body' : 'label'} weight={line.primary ? 'semibold' : 'regular'} tone={line.primary ? 'primary' : 'secondary'}>
                                    {t(line.label)}
                                </Text>
                            </Stack>
                            <Text role={line.primary ? 'title' : 'label'} weight={line.primary ? 'semibold' : 'medium'}>
                                <Ltr>{formatValue(line.value, line.format, money, t)}</Ltr>
                            </Text>
                        </Row>
                    ))}
                </Stack>
            </Stack>
        </Surface>
    );
}

interface NumberFieldProps {
    id: string;
    field: FieldSpec;
    value: number;
    money: Money;
    onChange: (value: number) => void;
}

/** A typed box and a slider on one value. The box keeps what is typed until it reads as a number in range,
 *  and shows thousands separators until it is focused for editing. */
function NumberField({ id, field, value, money, onChange }: NumberFieldProps) {
    const t = useT();
    const [draft, setDraft] = useState<string | null>(null);
    const [focused, setFocused] = useState(false);
    const sliderMax = Math.min(field.max, Math.max(field.sliderMax ?? field.max, value));
    const prefix = field.format === 'currency' ? currencySymbol(money) : '';
    const suffix = field.format === 'percent' ? '%' : field.format === 'years' ? t.bare('cards.yearsShort') : '';
    const label = t(field.label);

    const onType = (text: string) => {
        setDraft(text);
        const n = Number(text);
        if (text.trim() === '' || !Number.isFinite(n) || n < field.min || n > field.max) return;
        if (field.integer && !Number.isInteger(n)) return;
        onChange(n);
    };

    const shown = draft ?? (focused ? String(value) : new Intl.NumberFormat(money.locale, { maximumFractionDigits: 10 }).format(value));

    return (
        <Stack gap="xs">
            <Row gap="md">
                <Stack flex={1}>
                    <Text role="label" weight="medium" tone="secondary">
                        {label}
                    </Text>
                </Stack>
                <Stack flex={1} dir="ltr">
                    <TextField
                        value={shown}
                        onChangeText={onType}
                        keyboardType="decimal-pad"
                        accessibilityLabel={label}
                        leading={prefix ? <Text role="label" tone="secondary">{prefix}</Text> : undefined}
                        trailing={suffix ? <Stack paddingX="sm"><Text role="label" tone="secondary">{suffix}</Text></Stack> : undefined}
                        onFocus={() => setFocused(true)}
                        onBlur={() => {
                            setFocused(false);
                            setDraft(null);
                        }}
                        testID={`calc-input-${id}`}
                    />
                </Stack>
            </Row>
            <Slider
                value={value}
                min={field.min}
                max={sliderMax}
                step={field.step}
                onChange={(n) => {
                    setDraft(null);
                    onChange(n);
                }}
                accessibilityLabel={t('cards.slider', { name: label })}
                formatValue={(n) => formatValue(n, field.format, money, t)}
                testID={`calc-slider-${id}`}
            />
        </Stack>
    );
}
