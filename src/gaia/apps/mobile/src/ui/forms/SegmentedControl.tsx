// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated';
import { layoutDirection } from '@/platform/direction';
import { Pressable } from '../controls/Pressable';
import { ChoiceList, type ChoiceOption } from '../feedback/ChoiceList';
import { useTheme } from '../theme/ThemeProvider';
import { a11yVisibility } from '../theme/a11y';
import { Text } from '../text/Text';
import { motion } from '../tokens/generated';
import { segmentsFit } from './segments';

export interface SegmentedControlProps {
    /** `accessibilityLabel` is the full label a screen reader says when `label` is the bare one. */
    options: { value: string; label: string; accessibilityLabel?: string }[];
    value: string;
    onChange: (value: string) => void;
    accessibilityLabel?: string;
    testID?: string;
}

const HEIGHT = 48;
const PAD = 2;

// Falls back to a ChoiceList when a label wouldn't fit its segment on one line.
export function SegmentedControl({ options, value, onChange, accessibilityLabel, testID }: SegmentedControlProps) {
    const { colors, radius } = useTheme();
    // The track is always laid out LTR and mirrored by hand: iOS swaps `left` in RTL, the web doesn't.
    const rtl = layoutDirection() === 'rtl';
    const shown = rtl ? [...options].reverse() : options;
    const reduced = useReducedMotion();
    const [width, setWidth] = useState(0);
    const [labelWidths, setLabelWidths] = useState<number[]>([]);
    const index = Math.max(0, options.findIndex((o) => o.value === value));
    const slide = useSharedValue(index);

    useEffect(() => {
        slide.value = reduced ? index : withTiming(index, { duration: motion.duration.Fast });
    }, [index, reduced, slide]);

    const segment = Math.max(0, (width - PAD * 2) / Math.max(1, options.length));
    const indicatorStyle = useAnimatedStyle(() => {
        const x = (rtl ? options.length - 1 - slide.value : slide.value) * segment;
        return { transform: [{ translateX: x }] };
    });

    const measured = labelWidths.length === options.length && labelWidths.every((w) => w > 0);
    const fits = !measured || segmentsFit(labelWidths, width);

    return (
        <View testID={testID} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
            <View
                {...a11yVisibility({ accessibilityElementsHidden: true, importantForAccessibility: 'no-hide-descendants' })}
                pointerEvents="none"
                style={{ position: 'absolute', width: 4000, height: 0, overflow: 'hidden', flexDirection: 'row', alignItems: 'flex-start' }}
            >
                {options.map((o, i) => (
                    <View
                        key={o.value}
                        onLayout={(e) => {
                            const w = e.nativeEvent.layout.width;
                            setLabelWidths((prev) => (prev[i] === w ? prev : Object.assign([], prev, { [i]: w })));
                        }}
                    >
                        <Text role="ui" weight="semibold" compact>
                            {o.label}
                        </Text>
                    </View>
                ))}
            </View>
            {fits ? (
                <View
                    accessibilityRole="radiogroup"
                    accessibilityLabel={accessibilityLabel}
                    style={{
                        flexDirection: 'row',
                        direction: 'ltr',
                        height: HEIGHT,
                        padding: PAD,
                        borderRadius: radius.md,
                        backgroundColor: colors.bgActive,
                    }}
                >
                    <Animated.View
                        style={[
                            {
                                position: 'absolute',
                                top: PAD,
                                left: PAD,
                                width: segment,
                                height: HEIGHT - PAD * 2,
                                borderRadius: radius.md - 2,
                                backgroundColor: colors.bgCard,
                                borderWidth: 1,
                                borderColor: colors.border,
                            },
                            indicatorStyle,
                        ]}
                    />
                    {shown.map((o) => {
                        const on = o.value === value;
                        return (
                            <View key={o.value} style={{ flex: 1 }}>
                                <Pressable
                                    onPress={() => onChange(o.value)}
                                    accessibilityRole="radio"
                                    accessibilityLabel={o.accessibilityLabel ?? o.label}
                                    accessibilityState={{ checked: on }}
                                    minSize={HEIGHT - PAD * 2}
                                >
                                    <View style={{ alignItems: 'center', justifyContent: 'center', height: HEIGHT - PAD * 2 }}>
                                        <Text role="ui" weight={on ? 'semibold' : 'medium'} tone={on ? 'primary' : 'secondary'} numberOfLines={1} compact>
                                            {o.label}
                                        </Text>
                                    </View>
                                </Pressable>
                            </View>
                        );
                    })}
                </View>
            ) : (
                <ChoiceList options={options as ChoiceOption[]} value={value} onChange={onChange} accessibilityLabel={accessibilityLabel} />
            )}
        </View>
    );
}
