// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { View } from 'react-native';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { useDirection } from '@/i18n/useT';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import { motion } from '../tokens/generated';

export interface ProgressLineProps {
    /** 0 to 1; leave it out for an indeterminate line. */
    value?: number;
    label?: string;
}

const SEGMENT = 0.4;

export function ProgressLine({ value, label }: ProgressLineProps) {
    const { colors, radius, space } = useTheme();
    const rtl = useDirection() === 'rtl';
    const reduced = useReducedMotion();
    const [width, setWidth] = useState(0);
    const sweep = useSharedValue(0);
    const indeterminate = value === undefined;
    const clamped = indeterminate ? 0 : Math.min(1, Math.max(0, value));

    useEffect(() => {
        if (!indeterminate || reduced) {
            sweep.value = 0;
            return;
        }
        sweep.value = withRepeat(withTiming(1, { duration: motion.duration.Pulse, easing: Easing.inOut(Easing.ease) }), -1, false);
    }, [indeterminate, reduced, sweep]);

    const sweepStyle = useAnimatedStyle(() => {
        const travel = width * (1 + SEGMENT);
        const x = -width * SEGMENT + sweep.value * travel;
        return { transform: [{ translateX: rtl ? -x : x }] };
    });

    return (
        <View
            accessible
            accessibilityRole="progressbar"
            accessibilityLabel={label}
            accessibilityValue={indeterminate ? undefined : { min: 0, max: 100, now: Math.round(clamped * 100) }}
            style={{ gap: space.xs }}
        >
            {label ? (
                <Text role="caption" tone="secondary">
                    {label}
                </Text>
            ) : null}
            <View
                onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
                style={{ height: 4, borderRadius: radius.full, backgroundColor: colors.bgActive, overflow: 'hidden', flexDirection: 'row' }}
            >
                {indeterminate ? (
                    reduced ? (
                        <View style={{ width: `${SEGMENT * 100}%`, backgroundColor: colors.accentSolid, borderRadius: radius.full }} />
                    ) : (
                        <Animated.View style={[{ width: `${SEGMENT * 100}%`, backgroundColor: colors.accentSolid, borderRadius: radius.full }, sweepStyle]} />
                    )
                ) : (
                    <View style={{ width: `${clamped * 100}%`, backgroundColor: colors.accentSolid, borderRadius: radius.full }} />
                )}
            </View>
        </View>
    );
}
