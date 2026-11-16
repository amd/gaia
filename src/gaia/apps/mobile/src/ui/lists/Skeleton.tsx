// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View, type DimensionValue } from 'react-native';
import { useT } from '@/i18n/useT';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { motion } from '../tokens/generated';
import { Pulse } from './Pulse';
import { a11yVisibility } from '../theme/a11y';

export interface SkeletonProps {
    width?: DimensionValue;
    height?: number;
    circle?: boolean;
}

export function Skeleton({ width = '100%', height = 14, circle }: SkeletonProps) {
    const { colors, radius } = useTheme();
    return (
        <View
            {...a11yVisibility({ accessibilityElementsHidden: true, importantForAccessibility: 'no-hide-descendants' })}
            style={{ width: circle ? height : width, maxWidth: '100%' }}
        >
            <Pulse duration={motion.duration.Shimmer / 2}>
                <View style={{ height, width: '100%', borderRadius: circle ? radius.full : radius.sm, backgroundColor: colors.bgActive }} />
            </Pulse>
        </View>
    );
}

const WIDTHS = ['70%', '55%', '82%', '48%', '64%'] as const;

export function ListSkeleton({ rows = 5, label }: { rows?: number; label?: string }) {
    const t = useT();
    const { space } = useTheme();
    return (
        <View accessible accessibilityRole="progressbar" accessibilityLabel={label ?? t('pick.loading')} style={{ paddingVertical: space.sm }}>
            {Array.from({ length: rows }, (_, i) => (
                <View key={i} style={{ minHeight: 48, paddingHorizontal: space.lg, justifyContent: 'center' }}>
                    <Row gap="md">
                        <Skeleton circle height={20} />
                        <View style={{ flex: 1 }}>
                            <Skeleton width={WIDTHS[i % WIDTHS.length]} height={14} />
                        </View>
                    </Row>
                </View>
            ))}
        </View>
    );
}
