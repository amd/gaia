// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import { buildTiles } from '@webui/utils/resourceTiles.core';

import { useResourceUsage } from '@/data';
import { useT } from '@/i18n/useT';
import { Card, IconButton, Ltr, ProgressLine, Row, Stack, Text } from '@/ui';
import { X } from '@/ui/icons';

/** This PC's CPU, memory, GPU, NPU and network use, one line per part, while the strip is open. */
export function ResourceStrip({ onClose }: { onClose: () => void }) {
    const t = useT();
    const { data: usage, error } = useResourceUsage(true);
    const tiles = usage?.ready ? buildTiles(t, usage) : [];
    return (
        <Stack paddingX="lg" paddingY="xs">
            <Card padding="sm">
                <Row gap="sm" align="start" accessibilityRole="summary">
                    <Stack flex={1} gap="sm">
                        {tiles.length > 0 ? (
                            tiles.map((tile) => (
                                <Stack key={tile.key} gap="xs">
                                    <Row gap="sm" justify="between">
                                        <View style={{ flexShrink: 1 }}>
                                            <Text role="label" weight="medium" numberOfLines={1}>
                                                {tile.label}
                                            </Text>
                                        </View>
                                        <Ltr>{[tile.value, tile.detail].filter(Boolean).join(' · ')}</Ltr>
                                    </Row>
                                    {tile.history.length > 0 && tile.tone !== 'network' ? (
                                        <ProgressLine value={parseFloat(tile.value) / 100} />
                                    ) : null}
                                </Stack>
                            ))
                        ) : (
                            <Text role="label" tone={error ? 'danger' : 'muted'}>
                                {error ? t('resources.error') : t('resources.reading')}
                            </Text>
                        )}
                        {error && tiles.length > 0 ? (
                            <Text role="label" tone="danger">
                                {t('resources.error')}
                            </Text>
                        ) : null}
                    </Stack>
                    <IconButton icon={X} size="sm" label={t('resources.hide')} onPress={onClose} testID="resources-close" />
                </Row>
            </Card>
        </Stack>
    );
}
