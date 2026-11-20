// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { unsupportedView } from '@webui/utils/renderCards.core';

import { useT } from '@/i18n/useT';
import { Disclosure, HScroll, Stack, Surface, Text } from '@/ui';

export interface UnsupportedCardProps {
    /** 'unknown': no card for this render key; 'invalid': the card rejected the payload's shape. */
    variant: 'unknown' | 'invalid';
    render: string;
    data: unknown;
}

/** The loud path for a card the phone can't draw: what went wrong, and the payload behind a disclosure. */
export function UnsupportedCard({ variant, render, data }: UnsupportedCardProps) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const { pretty, bytes } = unsupportedView(variant, data);
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-unsupported">
            <Stack gap="sm">
                <Text role="label" weight="medium" dir="auto">
                    {variant === 'unknown' ? t('render.unsupportedType', { render }) : t('render.invalidPayload', { render })}
                </Text>
                <Disclosure title={t('render.rawPayload')} open={open} onToggle={() => setOpen((o) => !o)} testID="card-unsupported-details">
                    {pretty === null ? (
                        <Text role="caption" tone="secondary" italic>
                            {t('render.payloadTooLarge', { bytes })}
                        </Text>
                    ) : (
                        <HScroll dir="ltr">
                            <Text role="caption" mono selectable dir="ltr">
                                {pretty}
                            </Text>
                        </HScroll>
                    )}
                </Disclosure>
            </Stack>
        </Surface>
    );
}
