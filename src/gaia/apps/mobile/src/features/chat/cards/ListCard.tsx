// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { MAX_ITEMS, isListPayload } from '@webui/utils/renderCards.core';

import { useDirection } from '@/i18n/useT';
import { contentDir, Ltr, Row, Stack, Surface, Text } from '@/ui';

import { TruncationNote } from './TruncationNote';
import { UnsupportedCard } from './UnsupportedCard';

export function ListCard({ data }: { data: unknown }) {
    const uiDir = useDirection();
    if (!isListPayload(data)) return <UnsupportedCard variant="invalid" render="list" data={data} />;
    const dir = contentDir(`${data.title ?? ''} ${data.items.slice(0, 3).join(' ')}`, uiDir);
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-list">
            <Stack gap="sm" dir={dir}>
                {data.title ? (
                    <Text role="ui" weight="semibold" dir="auto" accessibilityRole="header">
                        {data.title}
                    </Text>
                ) : null}
                {data.items.slice(0, MAX_ITEMS).map((item, i) => (
                    <Row key={i} gap="sm" align="start" dir={dir}>
                        {data.ordered ? (
                            <Text role="label" tone="secondary">
                                <Ltr>{`${i + 1}.`}</Ltr>
                            </Text>
                        ) : (
                            <Text role="label" tone="secondary">
                                •
                            </Text>
                        )}
                        <Stack flex={1}>
                            <Text role="label" dir="auto" selectable>
                                {String(item)}
                            </Text>
                        </Stack>
                    </Row>
                ))}
                {data.items.length > MAX_ITEMS ? <TruncationNote total={data.items.length} /> : null}
            </Stack>
        </Surface>
    );
}
