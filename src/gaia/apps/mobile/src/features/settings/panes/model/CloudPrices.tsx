// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCloudPrices } from '@/data';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Divider, Notice, Row, Spinner, Stack, Text } from '@/ui';

import { isPcRefusal, PcOnlyNotice } from '../../PcOnlyNotice';
import { priceText } from './modelText';

/** A provider's list prices, read only; shown before it is connected. */
export function CloudPrices({ provider }: { provider: string }) {
    const t = useT();
    const prices = useCloudPrices(provider, true);
    const list = prices.data;

    if (isPcRefusal(prices.error)) return <PcOnlyNotice testID="prices-pc-only" />;
    if (prices.error) {
        return <Notice tone="danger" message={t.compose((say) => say('settings.price.loadFailed', { reason: describeFailureWith(prices.error, say) }))} />;
    }
    if (!list) {
        return (
            <Row gap="sm">
                <Spinner />
                <Text role="caption" tone="muted">{t('settings.price.loading')}</Text>
            </Row>
        );
    }
    if (list.models.length === 0) {
        return (
            <Text role="caption" tone="muted">
                {[t('settings.price.unknownSentence'), list.note].filter(Boolean).join(' ')}
            </Text>
        );
    }
    return (
        <Stack gap="sm">
            {list.models.map((m, i) => (
                <Stack key={m.model} gap="xs">
                    {i > 0 ? <Divider /> : null}
                    <Text role="ui" weight="medium">{m.name}</Text>
                    {m.note ? <Text role="caption" tone="muted">{m.note}</Text> : null}
                    <Text role="caption" tone="secondary">{priceText(m.price, t)}</Text>
                </Stack>
            ))}
            <Text role="caption" tone="muted">
                {list.source ? `${list.source}. ` : ''}
                {t('settings.price.payProvider')}
            </Text>
        </Stack>
    );
}
