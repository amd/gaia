// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { View } from 'react-native';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';

export function SectionHeader({ title, trailing }: { title: string; trailing?: ReactNode }) {
    const { colors, space } = useTheme();
    return (
        <View style={{ paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.xs, backgroundColor: colors.bgPrimary }}>
            <Row gap="sm" justify="between">
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text role="caption" weight="semibold" tone="muted" numberOfLines={1} accessibilityRole="header">
                        {title}
                    </Text>
                </View>
                {trailing}
            </Row>
        </View>
    );
}
