// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated';
import { useDirection } from '@/i18n/useT';
import { Pressable } from '../controls/Pressable';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import { motion } from '../tokens/generated';

export interface SwitchProps {
    value: boolean;
    onValueChange: (value: boolean) => void;
    label: string;
    description?: string;
    disabled?: boolean;
    testID?: string;
}

const TRACK_W = 48;
const TRACK_H = 28;
const KNOB = 20;
const INSET = (TRACK_H - KNOB) / 2;
const TRAVEL = TRACK_W - KNOB - INSET * 2;

// The whole row is the touch target; the knob slides toward the end edge when on.
export function Switch({ value, onValueChange, label, description, disabled, testID }: SwitchProps) {
    const { colors, radius, space } = useTheme();
    const rtl = useDirection() === 'rtl';
    const reduced = useReducedMotion();
    const progress = useSharedValue(value ? 1 : 0);

    useEffect(() => {
        const to = value ? 1 : 0;
        progress.value = reduced ? to : withTiming(to, { duration: motion.duration.Fast });
    }, [value, reduced, progress]);

    const knobStyle = useAnimatedStyle(() => {
        const x = progress.value * TRAVEL;
        return { transform: [{ translateX: rtl ? -x : x }] };
    });

    return (
        <Pressable
            onPress={() => onValueChange(!value)}
            disabled={disabled}
            accessibilityRole="switch"
            accessibilityLabel={description ? `${label}. ${description}` : label}
            accessibilityState={{ checked: value }}
            testID={testID}
        >
            <Row gap="md" align="center">
                <View style={{ flex: 1, minWidth: 0, paddingVertical: space.sm }}>
                    <Text role="ui" weight="medium">
                        {label}
                    </Text>
                    {description ? (
                        <Text role="caption" tone="muted">
                            {description}
                        </Text>
                    ) : null}
                </View>
                <View
                    style={{
                        width: TRACK_W,
                        height: TRACK_H,
                        borderRadius: radius.full,
                        borderWidth: 1,
                        borderColor: value ? colors.accent : colors.border,
                        backgroundColor: value ? colors.accentFill : colors.bgActive,
                        justifyContent: 'center',
                        paddingHorizontal: INSET - 1,
                    }}
                >
                    <Animated.View
                        style={[
                            { width: KNOB, height: KNOB, borderRadius: radius.full, backgroundColor: value ? colors.accentFillText : colors.textMuted },
                            knobStyle,
                        ]}
                    />
                </View>
            </Row>
        </Pressable>
    );
}
