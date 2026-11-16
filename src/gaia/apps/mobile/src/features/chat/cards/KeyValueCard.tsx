// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { MAX_ITEMS, isKeyValuePayload, scalarText } from '@webui/utils/renderCards.core';

import { useT } from '@/i18n/useT';
import { Stack, Surface, Text } from '@/ui';

import { TruncationNote } from './TruncationNote';
import { UnsupportedCard } from './UnsupportedCard';

export function KeyValueCard({ data }: { data: unknown }) {
    const t = useT();
    if (!isKeyValuePayload(data)) return <UnsupportedCard variant="invalid" render="key_value" data={data} />;
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-key-value">
            <Stack gap="md">
                {data.title ? (
                    <Text role="ui" weight="semibold" dir="auto" accessibilityRole="header">
                        {data.title}
                    </Text>
                ) : null}
                {data.items.slice(0, MAX_ITEMS).map((item, i) => {
                    const value = scalarText(item.value);
                    return (
                        <Stack key={i} gap="xs" accessibilityLabel={t.bare('mobileApp.render.keyValue', { key: item.key, value })}>
                            <Text role="caption" tone="secondary" dir="auto">
                                {item.key}
                            </Text>
                            <Text role="label" dir="auto" selectable>
                                {value}
                            </Text>
                        </Stack>
                    );
                })}
                {data.items.length > MAX_ITEMS ? <TruncationNote total={data.items.length} /> : null}
            </Stack>
        </Surface>
    );
}
