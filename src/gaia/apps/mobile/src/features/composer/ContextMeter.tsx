// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View } from 'react-native';

import { pluralKey } from '@webui/i18n/registry';
import { formatTokens } from '@webui/utils/format';

import type { ContextUsage } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Button, Ltr, Notice, Pressable, Row, Sheet, Stack, Text, useTheme } from '@/ui';

import { ChipMeasure, useChipCollapsed } from './ChipFitContext';

const PARTS = [
    { key: 'system', label: 'ctx.meter.part.system' },
    { key: 'tools', label: 'ctx.meter.part.tools' },
    { key: 'documents', label: 'ctx.meter.part.documents' },
    { key: 'history', label: 'ctx.meter.part.history' },
    { key: 'query', label: 'ctx.meter.part.query' },
] as const;

export interface ContextMeterProps {
    usage: ContextUsage | null;
    /** Why there is no reading yet; shown as a dash that opens that reason. */
    unavailable?: string | null;
    /** Opens the chat's panel, where the context and performance details are. */
    onDetails?: () => void;
    /** Starts with the breakdown sheet open (developer gallery). */
    initialOpen?: boolean;
}

/** How full the model's context window is; tap for what fills it. */
export function ContextMeter({ usage, unavailable, onDetails, initialOpen = false }: ContextMeterProps) {
    const t = useT();
    const { colors, radius } = useTheme();
    const [open, setOpen] = useState(initialOpen);
    const barless = useChipCollapsed('meter');
    if (!usage && !unavailable) return null;

    const pct = usage ? Math.min(100, Math.round((usage.used / usage.window) * 100)) : 0;
    const warn = !!usage && usage.at_risk.length > 0;
    const partColor = {
        system: colors.accentBlue,
        tools: colors.accentPurple,
        documents: colors.accentGreen,
        history: colors.accentSolid,
        query: colors.accentCyan,
    };
    const aria = usage
        ? t('ctx.meter.aria', {
              pct,
              used: formatTokens(usage.used),
              window: formatTokens(usage.window),
          })
        : `${t('ctx.context')}: ${unavailable}`;

    const capped = !!usage && usage.model_window != null && usage.model_window > usage.window;
    const hint = capped
        ? t('ctx.meter.titleCapped', { gaia: formatTokens(usage.window), model: formatTokens(usage.model_window!) })
        : t('ctx.meter.title');

    const reading = (noBar: boolean) => (
        <Row gap="sm">
            {noBar ? null : (
                <View
                    style={{
                        width: 40,
                        height: 4,
                        borderRadius: radius.full,
                        backgroundColor: colors.bgActive,
                        overflow: 'hidden',
                    }}
                >
                    <View
                        style={{
                            width: `${pct}%`,
                            height: 4,
                            backgroundColor: warn ? colors.accentYellow : colors.accentSolid,
                        }}
                    />
                </View>
            )}
            <Text role="caption" tone={warn ? 'warning' : 'secondary'}>
                {usage ? <Ltr>{`${pct}%`}</Ltr> : `${t.bare('ctx.context')} –`}
            </Text>
        </Row>
    );

    return (
        <>
            <ChipMeasure id="meter">{reading(false)}</ChipMeasure>
            <Pressable
                onPress={() => setOpen(true)}
                accessibilityLabel={aria}
                accessibilityHint={usage ? hint : unavailable ?? undefined}
                accessibilityState={{ expanded: open }}
                testID="context-meter"
            >
                {reading(barless)}
            </Pressable>
            <Sheet open={open} onClose={() => setOpen(false)} title={t('ctx.meter.window')}>
                {usage ? <Breakdown usage={usage} colors={partColor} onDetails={onDetails ? () => { setOpen(false); onDetails(); } : undefined} /> : <Text role="body">{unavailable}</Text>}
            </Sheet>
        </>
    );
}

function Breakdown({ usage, colors: partColor, onDetails }: { usage: ContextUsage; colors: Record<(typeof PARTS)[number]['key'], string>; onDetails?: () => void }) {
    const t = useT();
    const { colors, radius } = useTheme();
    const free = Math.max(0, usage.window - usage.used - usage.reply_reserve);
    const modelWindow = usage.model_window != null && usage.model_window > usage.window ? usage.model_window : null;
    const setup = usage.system + usage.tools;
    const mostlySetup = usage.used > 0 && setup / usage.used >= 0.6;
    return (
        <Stack gap="md">
            <Text role="body">
                <Text role="body" weight="semibold">
                    <Ltr>{formatTokens(usage.used)}</Ltr>
                </Text>{' '}
                {t.bare('ctx.of')} <Ltr>{formatTokens(usage.window)}</Ltr> {t.bare('ctx.tokensWord')}
            </Text>
            {modelWindow ? (
                <Text role="caption" tone="secondary">
                    {t('ctx.meter.capNote', { gaia: formatTokens(usage.window), model: formatTokens(modelWindow) })}
                </Text>
            ) : null}
            {mostlySetup ? (
                <Text role="caption" tone="secondary">
                    {t('ctx.meter.mostlySetup', {
                        setup: formatTokens(setup),
                        chat: formatTokens(usage.history + usage.query + usage.documents),
                    })}
                </Text>
            ) : null}
            <View
                style={{
                    flexDirection: 'row',
                    height: 8,
                    borderRadius: radius.full,
                    backgroundColor: colors.bgActive,
                    overflow: 'hidden',
                }}
            >
                {PARTS.map(({ key }) => (
                    <View
                        key={key}
                        style={{
                            width: `${(usage[key] / usage.window) * 100}%`,
                            backgroundColor: partColor[key],
                        }}
                    />
                ))}
            </View>
            <Stack gap="sm">
                {PARTS.filter(({ key }) => key !== 'query' || usage.query > 0).map(({ key, label }) => (
                    <Row key={key} gap="sm" justify="between">
                        <Row gap="sm">
                            <View
                                style={{
                                    width: 10,
                                    height: 10,
                                    borderRadius: radius.full,
                                    backgroundColor: partColor[key],
                                }}
                            />
                            <Text role="body">{t(label)}</Text>
                        </Row>
                        <Text role="body">
                            <Ltr>{formatTokens(usage[key])}</Ltr>
                        </Text>
                    </Row>
                ))}
                <Row gap="sm" justify="between">
                    <Text role="caption" tone="secondary">
                        {t('ctx.meter.reserve')}
                    </Text>
                    <Text role="caption" tone="secondary">
                        <Ltr>{formatTokens(usage.reply_reserve)}</Ltr>
                    </Text>
                </Row>
                <Row gap="sm" justify="between">
                    <Text role="caption" tone="secondary">
                        {t('ctx.freeRow')}
                    </Text>
                    <Text role="caption" tone="secondary">
                        <Ltr>{formatTokens(free)}</Ltr>
                    </Text>
                </Row>
            </Stack>
            <Text role="caption" tone="secondary">
                {t.compose((say) =>
                    [
                        say(pluralKey('ctx.meter.inView', usage.turns_in_view), {
                            n: usage.turns_in_view,
                        }),
                        usage.turns_hidden > 0
                            ? say(pluralKey('ctx.meter.hidden', usage.turns_hidden), {
                                  n: usage.turns_hidden,
                              })
                            : '',
                        '. ',
                        say(usage.tokenizer?.exact ? 'ctx.meter.exact' : 'ctx.meter.estimates'),
                        ' ',
                        say('ctx.meter.saved'),
                    ].join(''),
                )}
            </Text>
            {onDetails ? <Button variant="secondary" label={t('ctx.meter.details')} onPress={onDetails} testID="context-details" /> : null}
        </Stack>
    );
}

/** One line above the box when older turns are about to leave the model's view; details on request. */
export function ContextWarning({ usage }: { usage: ContextUsage | null }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    // Dismissed for these turns only; another turn coming up to the edge shows it again.
    const [dismissed, setDismissed] = useState<string | null>(null);
    if (!usage || usage.at_risk.length === 0) return null;
    const key = usage.at_risk.join('\n');
    if (dismissed === key) return null;
    return (
        <Stack paddingX="lg">
            <Notice
                tone="warning"
                message={
                    open
                        ? `${t.plural('ctx.meter.warning', usage.at_risk.length)} ${usage.at_risk.map((q) => `“${q}”`).join(', ')}. ${t('ctx.meter.warningSaved')}`
                        : t('ctx.warning.short')
                }
                action={{
                    label: open ? t('ctx.warning.hideDetails') : t('ctx.warning.details'),
                    onPress: () => setOpen((v) => !v),
                }}
                onDismiss={() => {
                    setDismissed(key);
                    setOpen(false);
                }}
                testID="context-warning"
            />
        </Stack>
    );
}
