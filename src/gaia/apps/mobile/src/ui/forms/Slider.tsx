// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { View, type AccessibilityActionEvent } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import { layoutDirection } from '@/platform/direction';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Ltr } from '../text/Ltr';
import { Text } from '../text/Text';
import { snapValue } from './sliderMath';

export interface SliderProps {
    value: number;
    onChange: (value: number) => void;
    /** Fires once when the finger lifts, with the final value. */
    onChangeEnd?: (value: number) => void;
    min?: number;
    max?: number;
    step?: number;
    label?: string;
    /** The accessible name when no visible `label` is drawn. */
    accessibilityLabel?: string;
    /** The text shown beside the label, e.g. `${v}%`. Defaults to the number. */
    formatValue?: (value: number) => string;
    disabled?: boolean;
    testID?: string;
}

const THUMB = 28;

export function Slider({ value, onChange, onChangeEnd, min = 0, max = 100, step = 1, label, accessibilityLabel, formatValue, disabled, testID }: SliderProps) {
    const { colors, radius } = useTheme();
    // The track is always laid out LTR and mirrored by hand: iOS swaps `left`/`start` in RTL, the web doesn't.
    const rtl = layoutDirection() === 'rtl';
    const [width, setWidth] = useState(0);
    const latest = useRef(value);
    useEffect(() => {
        latest.current = value;
    }, [value]);

    const range = Math.max(max - min, Number.EPSILON);
    const fraction = Math.min(1, Math.max(0, (value - min) / range));
    const room = Math.max(0, width - THUMB);
    const text = formatValue ? formatValue(value) : String(value);

    const moveTo = (x: number) => {
        if (disabled || room <= 0) return;
        const p = Math.min(1, Math.max(0, (rtl ? width - x : x) - THUMB / 2) / room);
        const next = snapValue(min + p * range, min, max, step);
        if (next !== latest.current) {
            latest.current = next;
            onChange(next);
        }
    };

    const pan = usePanGesture({
        runOnJS: true,
        enabled: !disabled,
        minDistance: 0,
        onBegin: (e) => moveTo(e.x),
        onUpdate: (e) => moveTo(e.x),
        onDeactivate: () => onChangeEnd?.(latest.current),
    });

    const nudge = (dir: 1 | -1) => {
        const next = snapValue(latest.current + dir * (step > 0 ? step : range / 10), min, max, step);
        latest.current = next;
        onChange(next);
        onChangeEnd?.(next);
    };

    return (
        <View style={{ opacity: disabled ? 0.45 : 1 }}>
            {label ? (
                <Row gap="sm">
                    <View style={{ flex: 1, minWidth: 0 }}>
                        <Text role="label" weight="medium" tone="secondary">
                            {label}
                        </Text>
                    </View>
                    <Ltr>{text}</Ltr>
                </Row>
            ) : null}
            <GestureDetector gesture={pan}>
                <View
                    accessible
                    accessibilityRole="adjustable"
                    accessibilityLabel={accessibilityLabel ?? label}
                    accessibilityValue={{ min, max, now: value, text }}
                    accessibilityState={{ disabled: !!disabled }}
                    accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
                    onAccessibilityAction={(e: AccessibilityActionEvent) => {
                        if (disabled) return;
                        if (e.nativeEvent.actionName === 'increment') nudge(1);
                        else if (e.nativeEvent.actionName === 'decrement') nudge(-1);
                    }}
                    onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
                    testID={testID}
                    style={{ height: 48, justifyContent: 'center', direction: 'ltr' }}
                >
                    <View style={{ height: 4, borderRadius: radius.full, backgroundColor: colors.bgActive, marginHorizontal: THUMB / 2 }}>
                        <View style={{ width: `${fraction * 100}%`, alignSelf: rtl ? 'flex-end' : 'flex-start', height: 4, borderRadius: radius.full, backgroundColor: colors.accentSolid }} />
                    </View>
                    <View
                        style={{
                            position: 'absolute',
                            left: (rtl ? 1 - fraction : fraction) * room,
                            width: THUMB,
                            height: THUMB,
                            borderRadius: radius.full,
                            backgroundColor: colors.bgCard,
                            borderWidth: 2,
                            borderColor: colors.accentSolid,
                        }}
                    />
                </View>
            </GestureDetector>
        </View>
    );
}
