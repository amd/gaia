// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';

import { diffBodyLines, diffLineKind, diffTitle, isDiffPayload } from '@webui/utils/renderCards.core';

import { Badge, HScroll, Stack, Surface, Text, useTheme } from '@/ui';

import { UnsupportedCard } from './UnsupportedCard';

const GUTTER = { added: '+', removed: '-', hunk: '', context: ' ' } as const;

export function DiffCard({ data }: { data: unknown }) {
    const { colors, space } = useTheme();
    if (!isDiffPayload(data)) return <UnsupportedCard variant="invalid" render="diff" data={data} />;
    const lines = data.unified ? diffBodyLines(data.unified) : [];
    const title = diffTitle(data);
    const fill = { added: colors.accentGreenDim, removed: colors.dangerDim, hunk: undefined, context: undefined };
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-diff">
            <Stack gap="sm">
                {title || data.badge ? (
                    <Stack gap="xs">
                        {data.badge ? <Badge label={data.badge} /> : null}
                        {title ? (
                            <Text role="ui" weight="semibold" dir="auto" accessibilityRole="header">
                                {title}
                            </Text>
                        ) : null}
                    </Stack>
                ) : null}
                {data.path ? (
                    <Text role="caption" tone="secondary" dir="ltr">
                        {data.path}
                    </Text>
                ) : null}
                {data.note ? (
                    <Text role="caption" tone="secondary" dir="auto">
                        {data.note}
                    </Text>
                ) : null}
                {lines.length > 0 ? (
                    <HScroll dir="ltr" hint testID="card-diff-scroll">
                        <View style={{ minWidth: '100%', direction: 'ltr' }}>
                            {lines.map((line, i) => {
                                const kind = diffLineKind(line);
                                return (
                                    <View key={i} testID={`diff-line-${kind}`} style={{ flexDirection: 'row', backgroundColor: fill[kind], paddingHorizontal: space.xs }}>
                                        <Text role="caption" mono tone={kind === 'context' ? 'primary' : kind === 'hunk' ? 'secondary' : kind === 'added' ? 'success' : 'danger'}>
                                            {kind === 'hunk' ? line : `${GUTTER[kind]} `}
                                        </Text>
                                        {kind === 'hunk' ? null : (
                                            <Text role="caption" mono dir="ltr">
                                                {line.slice(kind === 'context' && !line.startsWith(' ') ? 0 : 1) || ' '}
                                            </Text>
                                        )}
                                    </View>
                                );
                            })}
                        </View>
                    </HScroll>
                ) : null}
            </Stack>
        </Surface>
    );
}
