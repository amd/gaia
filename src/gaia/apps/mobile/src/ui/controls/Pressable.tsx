// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import {
    Platform,
    Pressable as RNPressable,
    View,
    type AccessibilityActionEvent,
    type AccessibilityActionInfo,
    type AccessibilityRole,
    type AccessibilityState,
} from 'react-native';
import { useTheme } from '../theme/ThemeProvider';

export const PRESSABLE_MIN_SIZE = 48;

/** Android shows press feedback as the Material ripple (the `ripple` color token); iOS and web a highlight. */
export const RIPPLE = Platform.OS === 'android';

export interface PressState {
    pressed: boolean;
    /** Draw the pressed look (the pressed background token): true while pressed, except on Android, whose ripple is the feedback. */
    highlight: boolean;
}

export interface PressableProps {
    children?: ReactNode | ((state: PressState) => ReactNode);
    onPress?: () => void;
    onLongPress?: () => void;
    disabled?: boolean;
    accessibilityLabel?: string;
    accessibilityHint?: string;
    accessibilityRole?: AccessibilityRole;
    accessibilityState?: AccessibilityState;
    /** Extra screen-reader actions, e.g. what a long-press does. */
    accessibilityActions?: readonly AccessibilityActionInfo[];
    onAccessibilityAction?: (event: AccessibilityActionEvent) => void;
    /** False lets a screen reader reach the children (links) instead of reading one element. */
    accessible?: boolean;
    /** Minimum hit area in points; 48 unless a parent already guarantees it. */
    minSize?: number;
    /** Sizes to its content (a short label in a row of text); a horizontal hit slop keeps the target `minSize` wide. */
    inline?: boolean;
    /** Android: an icon-only control's borderless ripple, a circle of half the target. */
    round?: boolean;
    /**
     * Android: clip the ripple to a drawn shape smaller than the target, with these corners. `hug` keeps the shape at
     * its content's width at the start; otherwise it fills the target's width.
     */
    shape?: { radius: number; hug?: boolean };
    testID?: string;
}

export function Pressable({
    children,
    onPress,
    onLongPress,
    disabled,
    accessibilityLabel,
    accessibilityHint,
    accessibilityRole = 'button',
    accessibilityState,
    accessibilityActions,
    onAccessibilityAction,
    accessible,
    minSize = PRESSABLE_MIN_SIZE,
    inline,
    round,
    shape,
    testID,
}: PressableProps) {
    const { colors } = useTheme();
    const [width, setWidth] = useState(0);
    const [box, setBox] = useState<{ width: number; height: number } | null>(null);
    const clip = RIPPLE && shape != null;
    const slop = inline ? Math.max(0, Math.ceil((minSize - width) / 2)) : 0;
    // A clipped shape keeps the full target through hit slop, since its own bounds are smaller.
    const ringX = clip && box ? Math.max(0, Math.ceil((minSize - box.width) / 2)) : 0;
    const ringY = clip && box ? Math.max(0, Math.ceil((minSize - box.height) / 2)) : 0;
    const hitSlop = slop || ringX || ringY ? { left: slop + ringX, right: slop + ringX, top: ringY, bottom: ringY } : undefined;
    const pressable = (
        <RNPressable
            onPress={onPress}
            onLongPress={onLongPress}
            disabled={disabled}
            accessibilityRole={accessibilityRole}
            accessibilityLabel={accessibilityLabel}
            accessibilityHint={accessibilityHint}
            accessible={accessible}
            accessibilityActions={accessibilityActions}
            onAccessibilityAction={onAccessibilityAction}
            accessibilityState={{ ...accessibilityState, disabled: !!disabled }}
            testID={testID}
            hitSlop={hitSlop}
            android_ripple={
                RIPPLE
                    ? round
                        ? { color: colors.ripple, borderless: true, radius: minSize / 2, foreground: true }
                        : { color: colors.ripple, borderless: false, foreground: true }
                    : undefined
            }
            onLayout={inline ? (e) => setWidth(e.nativeEvent.layout.width) : undefined}
            style={({ pressed }) => ({
                minWidth: inline || clip ? undefined : minSize,
                minHeight: clip ? undefined : minSize,
                justifyContent: 'center',
                opacity: disabled ? 0.45 : pressed && !RIPPLE ? 0.72 : 1,
            })}
        >
            {({ pressed }) => (typeof children === 'function' ? children({ pressed, highlight: pressed && !RIPPLE }) : children)}
        </RNPressable>
    );
    if (!clip) return pressable;
    // A view's own overflow doesn't clip its foreground, where the ripple draws, so the shape is clipped from outside;
    // the clipping view takes the same hit slop, because touches outside its bounds never reach its children.
    return (
        <View testID={testID ? `${testID}-target` : undefined} style={{ minHeight: minSize, justifyContent: 'center', alignItems: shape.hug ? 'flex-start' : 'stretch' }}>
            <View
                testID={testID ? `${testID}-clip` : undefined}
                hitSlop={hitSlop}
                onLayout={(e) => setBox({ width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height })}
                style={{ overflow: 'hidden', borderRadius: shape.radius }}
            >
                {pressable}
            </View>
        </View>
    );
}
