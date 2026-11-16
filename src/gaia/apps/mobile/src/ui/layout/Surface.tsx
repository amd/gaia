// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import type { ReactNode } from 'react';
import { useTheme, type Theme } from '../theme/ThemeProvider';
import type { Space } from '../tokens/space';
import { Pressable } from '../controls/Pressable';

export type SurfaceTone = 'primary' | 'secondary' | 'card' | 'recessed' | 'userMsg' | 'accent';
export type Elevation = 'none' | 'sm' | 'md' | 'lg';

export interface SurfaceProps {
    children?: ReactNode;
    tone?: SurfaceTone;
    radius?: keyof Theme['radius'] | 'none';
    padding?: Space;
    /** `accent` draws the border in the accent color, for a card that needs an answer. */
    border?: boolean | 'accent';
    elevation?: Elevation;
    /** A panel that interrupts and needs an answer; screen readers announce it as a dialog. */
    role?: 'alertdialog';
    accessibilityLabel?: string;
    testID?: string;
}

export function useSurfaceStyle({ tone = 'primary', radius, padding, border, elevation = 'none' }: SurfaceProps) {
    const { colors, radius: radii, space, shadows } = useTheme();
    const bg = {
        primary: colors.bgPrimary,
        secondary: colors.bgSecondary,
        card: colors.bgCard,
        recessed: colors.bgRecessed,
        userMsg: colors.bgUserMsg,
        accent: colors.accentDim,
    }[tone];
    return {
        backgroundColor: bg,
        borderRadius: radius === undefined || radius === 'none' ? 0 : radii[radius],
        ...(padding ? { padding: space[padding] } : null),
        ...(border
            ? { borderWidth: 1, borderColor: border === 'accent' ? colors.accentLight : tone === 'recessed' ? colors.borderRecessed : colors.borderLight }
            : null),
        ...(elevation !== 'none' ? { boxShadow: shadows[elevation] } : null),
    } as const;
}

export function Surface(props: SurfaceProps) {
    const style = useSurfaceStyle(props);
    return (
        <View
            style={style}
            testID={props.testID}
            role={props.role}
            accessibilityLabel={props.accessibilityLabel}
            accessibilityLiveRegion={props.role ? 'polite' : undefined}
        >
            {props.children}
        </View>
    );
}

export interface CardProps {
    children?: ReactNode;
    padding?: Space;
    /** `flat` is the desktop's bordered content card; `raised` keeps a soft shadow for a card that floats. */
    variant?: 'flat' | 'raised';
    onPress?: () => void;
    accessibilityLabel?: string;
    testID?: string;
}

export function Card({ children, padding = 'lg', variant = 'flat', onPress, accessibilityLabel, testID }: CardProps) {
    const style = useSurfaceStyle(
        variant === 'raised'
            ? { tone: 'card', radius: 'lg', padding, elevation: 'sm' }
            : { tone: 'card', radius: 'lg', padding, border: true },
    );
    if (!onPress) {
        return (
            <View style={style} testID={testID}>
                {children}
            </View>
        );
    }
    return (
        <Pressable onPress={onPress} accessibilityLabel={accessibilityLabel} testID={testID} minSize={0}>
            <View style={style}>{children}</View>
        </Pressable>
    );
}

/** `accent` and `clear` are the 2 px underline of a tab strip: drawn in the accent color, or kept as empty space. */
export function Divider({ inset = false, vertical = false, line }: { inset?: boolean; vertical?: boolean; line?: 'accent' | 'clear' }) {
    const { colors, space } = useTheme();
    if (line) return <View style={{ height: 2, backgroundColor: line === 'accent' ? colors.accent : 'transparent' }} />;
    if (vertical) return <View style={{ width: 3, alignSelf: 'stretch', borderRadius: 2, backgroundColor: colors.border }} />;
    return <View style={{ height: 1, backgroundColor: colors.borderLight, marginStart: inset ? space.lg : 0 }} />;
}
