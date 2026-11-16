// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { describeError, useT } from '@/i18n/useT';
import { Badge, Card, Disclosure, Ltr, Row, Spinner, Stack, Text } from '@/ui';

import type { OfferState } from './offerState';

/** The two server choices, read-only: saving the choice acts on the PC, so a phone only shows it. */
function ReadOnlyChoices({ found }: { found: Extract<OfferState, { kind: 'offer' }>['found'] }) {
    const t = useT();
    const [more, setMore] = useState(false);
    const options = [
        { mode: 'gaia' as const, title: 'setup.offer.useOwn' as const, note: 'setup.offer.ownNote' as const },
        { mode: 'external' as const, title: 'setup.offer.useMine' as const, note: 'setup.offer.mineNote' as const },
    ];
    return (
        <Card>
            <Stack gap="md">
                <Text weight="semibold" accessibilityLabel={t('setup.offer.label')}>{t('setup.offer.title')}</Text>
                {options.map((o) => (
                    <Stack key={o.mode} gap="xs">
                        <Row gap="sm" wrap align="center">
                            <Text weight="medium">{t(o.title)}</Text>
                            {o.mode === 'gaia' ? <Badge tone="accent" label={t('setup.offer.recommended')} /> : null}
                        </Row>
                        <Text role="caption" tone="secondary">{t(o.note)}</Text>
                    </Stack>
                ))}
                <Disclosure title={t('setup.offer.whatGaiaDoes')} open={more} onToggle={() => setMore((m) => !m)}>
                    <Stack gap="xs">
                        <Ltr>{`Lemonade${found.version ? ` ${found.version}` : ''} · ${found.url}`}</Ltr>
                        <Text role="caption" tone="secondary">{t('setup.offer.whatGaiaDoesText')}</Text>
                    </Stack>
                </Disclosure>
                <Text role="caption" tone="secondary">{t('setup.check.pcOnly')}</Text>
            </Stack>
        </Card>
    );
}

/** "This PC already runs a model server": looking, the offer, what the PC chose, or why it couldn't tell. */
export function CheckOffer({ state }: { state: OfferState }) {
    const t = useT();
    const [details, setDetails] = useState(false);
    switch (state.kind) {
        case 'looking':
            return (
                <Row gap="sm" align="center">
                    <Spinner />
                    <Text role="caption" tone="secondary" accessibilityRole="summary">{t('setup.offer.looking')}</Text>
                </Row>
            );
        case 'error':
            return (
                <Stack gap="sm">
                    <Text tone="secondary">{t('setup.offer.checkFailed')}</Text>
                    <Disclosure title={t('setup.progress.details')} open={details} onToggle={() => setDetails((d) => !d)}>
                        <Ltr>{describeError(state.error, t)}</Ltr>
                    </Disclosure>
                </Stack>
            );
        case 'noAnswer':
            return <Text tone="secondary">{t('setup.offer.noAnswer', { url: state.urls.join(', ') })}</Text>;
        case 'tooOld':
            return (
                <Stack gap="xs">
                    <Text tone="secondary" accessibilityRole="summary">{t('setup.offer.tooOld')}</Text>
                    <Ltr>{`Lemonade ${state.found.version ?? ''} · ${state.found.url}`}</Ltr>
                </Stack>
            );
        case 'offer':
            return <ReadOnlyChoices found={state.found} />;
        case 'chosen':
            return (
                <Text tone="secondary" accessibilityRole="summary">
                    {state.mode === 'external'
                        ? t('setup.offer.usingYours', { url: state.url ?? '' })
                        : t('setup.offer.usingOwn')}
                </Text>
            );
        default:
            return null;
    }
}
