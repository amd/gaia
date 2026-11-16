// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { formatTotal } from '@webui/components/settings/cloudPrice';
import { readLimitDraft } from '@webui/utils/spendingLimit.core';

import { useAllowSpendingMonth, useSetSpendingLimit, useSpending } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Ltr, Notice, Row, Spinner, Stack, Text, TextField } from '@/ui';

import { isPcRefusal, PcOnlyNotice } from '../../PcOnlyNotice';
import { errorText, isolate } from './modelText';

/** This month's cloud spending at list price, with the monthly limit; shown for cloud providers. */
export function CloudSpending() {
    const t = useT();
    const spending = useSpending();
    const setLimit = useSetSpendingLimit();
    const allow = useAllowSpendingMonth();
    const [draft, setDraft] = useState<string | null>(null);
    const report = spending.data;

    if (!report) {
        return isPcRefusal(spending.error) ? (
            <PcOnlyNotice testID="spending-pc-only" />
        ) : spending.error ? (
            <Notice
                tone="danger"
                message={errorText(spending.error, t)}
                action={{ label: t('settings.key.checkAgain'), onPress: () => void spending.refetch() }}
            />
        ) : (
            <Row gap="sm">
                <Spinner />
                <Text role="caption" tone="muted">{t('settings.spend.loading')}</Text>
            </Row>
        );
    }

    const limit = report.limit_usd;
    const text = draft ?? (limit != null ? String(limit) : '');
    const { parsed, valid, dirty } = readLimitDraft(text, limit);
    const busy = setLimit.isPending || allow.isPending;
    const failure = setLimit.error ?? allow.error;
    const save = (value: number | null) => {
        setLimit.reset();
        allow.reset();
        setLimit.mutate(value, { onSuccess: () => setDraft(null) });
    };

    return (
        <Stack gap="sm" testID="cloud-spending">
            <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">{t('settings.spend.title')}</Text>
            <Stack gap="xs">
                <Text role="title" weight="semibold"><Ltr>{formatTotal(report.total_usd)}</Ltr></Text>
                {limit != null ? <Text role="ui" tone="secondary">{t('settings.spend.ofLimit', { limit: isolate(formatTotal(limit)) })}</Text> : null}
                <Text role="caption" tone="muted">
                    {t.plural('settings.spend.turns', report.turns - report.unpriced_turns - report.unreported_turns)}
                </Text>
            </Stack>
            {report.unpriced_turns > 0 ? (
                <Text role="caption" tone="muted">{t.plural('settings.spend.unpriced', report.unpriced_turns)}</Text>
            ) : null}
            {report.unreported_turns > 0 ? (
                <Text role="caption" tone="muted">{t.plural('settings.spend.unreported', report.unreported_turns)}</Text>
            ) : null}
            {report.state === 'near' ? <Notice tone="warning" message={t('settings.spend.near')} /> : null}
            {report.state === 'reached' ? (
                <Notice
                    tone="danger"
                    message={t('mobileApp.settings.spend.reached')}
                    action={{ label: t('settings.spend.allow'), onPress: () => { setLimit.reset(); allow.mutate(); } }}
                />
            ) : null}
            {report.state === 'allowed_over' ? <Text role="caption" tone="muted">{t('settings.spend.allowed')}</Text> : null}

            <TextField
                label={t('settings.spend.limit')}
                value={text}
                onChangeText={setDraft}
                placeholder={t.bare('settings.spend.noLimit')}
                keyboardType="decimal-pad"
                error={text.trim() !== '' && !valid ? t('mobileApp.spend.limitInvalid') : undefined}
                disabled={busy}
                testID="spend-limit-input"
            />
            <Button
                variant="secondary"
                full
                label={t('settings.advanced.save')}
                disabled={busy || !valid || !dirty}
                busy={setLimit.isPending && setLimit.variables !== null}
                onPress={() => save(parsed)}
                testID="spend-limit-save"
            />
            {limit != null ? (
                <Button
                    variant="secondary"
                    full
                    label={t('settings.spend.removeLimit')}
                    disabled={busy}
                    busy={setLimit.isPending && setLimit.variables === null}
                    onPress={() => { setDraft(null); save(null); }}
                />
            ) : null}
            <Text role="caption" tone="muted">{t('settings.spend.how')}</Text>
            {failure && isPcRefusal(failure) ? <PcOnlyNotice testID="spending-pc-only" /> : failure ? <Notice tone="danger" message={errorText(failure, t)} /> : null}
        </Stack>
    );
}
