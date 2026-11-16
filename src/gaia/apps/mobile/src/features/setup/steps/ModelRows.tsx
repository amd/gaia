// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';

import { cantRunHere, isNpu } from '@webui/components/setup/selection';
import type { SetupCatalogEntry } from '@webui/types';

import { useLocaleCode, useT } from '@/i18n/useT';
import { Badge, Pressable, Row, Stack, Text } from '@/ui';

import { statusText, tooBigText } from './modelsView';

/** A model's support chip: Verified or Untested; nothing for an optional model. */
export function SupportBadge({ entry }: { entry: SetupCatalogEntry }) {
    const t = useT();
    if (entry.support === 'verified') return <Badge label={t('setup.label.verified')} tone="success" />;
    if (entry.support === 'untested') return <Badge label={t('setup.label.untested')} tone="warning" />;
    return null;
}

/** A pressable row with a radio or checkbox mark, its name and chips, and what it says below. */
export function MarkedRow({ kind, checked, disabled, name, chips, onPress, children, label }: {
    kind: 'radio' | 'checkbox';
    checked: boolean;
    disabled?: boolean;
    name: ReactNode;
    chips?: ReactNode;
    onPress: () => void;
    children?: ReactNode;
    label: string;
}) {
    const mark = kind === 'radio' ? (checked ? '●' : '○') : checked ? '☑' : '☐';
    return (
        <Pressable
            onPress={onPress}
            disabled={disabled}
            accessibilityRole={kind}
            accessibilityLabel={label}
            accessibilityState={{ checked, disabled: !!disabled }}
        >
            <Row gap="sm" align="start">
                <Text tone={checked ? 'accent' : 'muted'}>{mark}</Text>
                <Stack flex={1} gap="xs">
                    <Row gap="sm" wrap>
                        {name}
                        {chips}
                    </Row>
                    {children}
                </Stack>
            </Row>
        </Pressable>
    );
}

export function Warn({ message }: { message: string }) {
    return <Text role="caption" tone="warning">{`⚠ ${message}`}</Text>;
}

/** One chat or image-reading model: name, chips, status and why it can't run here. */
export function ModelRadio({ entry: e, checked, recommended, disabled, onPick }: {
    entry: SetupCatalogEntry;
    checked: boolean;
    recommended: boolean;
    disabled?: boolean;
    onPick: () => void;
}) {
    const t = useT();
    const locale = useLocaleCode();
    return (
        <MarkedRow
            kind="radio"
            checked={checked}
            disabled={disabled || cantRunHere(e)}
            label={e.name}
            onPress={onPick}
            name={<Text role="ui" weight="medium" dir="ltr">{e.name}</Text>}
            chips={
                <>
                    {recommended ? <Badge label={t('setup.models.recommended')} tone="accent" /> : null}
                    <SupportBadge entry={e} />
                    {isNpu(e) ? <Badge label={t('setup.models.npu')} /> : null}
                </>
            }
        >
            <Text role="caption" tone="secondary">{statusText(e, t, locale)}</Text>
            {e.fits === false ? <Warn message={tooBigText(e, t, locale)} /> : null}
        </MarkedRow>
    );
}
