// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import type { Tone } from '../theme/tone';

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

export interface BadgeProps {
    label: string;
    tone?: BadgeTone;
    /** `tag` is the desktop's status tag (6 px corners); `pill` is for counts. */
    shape?: 'tag' | 'pill';
    /** What a screen reader says instead of the visible label. */
    accessibilityLabel?: string;
}

export function Badge({ label, tone = 'neutral', shape = 'tag', accessibilityLabel }: BadgeProps) {
    const { colors, radius } = useTheme();
    const look: Record<BadgeTone, { bg: string; text: Tone }> = {
        neutral: { bg: colors.bgTertiary, text: 'secondary' },
        accent: { bg: colors.accentDim, text: 'accent' },
        success: { bg: colors.accentGreenDim, text: 'success' },
        warning: { bg: colors.accentYellowDim, text: 'warning' },
        danger: { bg: colors.dangerDim, text: 'danger' },
    };
    const v = look[tone];
    return (
        <View
            accessible={accessibilityLabel !== undefined}
            accessibilityLabel={accessibilityLabel}
            style={{ alignSelf: 'flex-start', backgroundColor: v.bg, borderRadius: shape === 'pill' ? radius.full : radius.sm, paddingHorizontal: 8, paddingVertical: 2, minWidth: 20 }}
        >
            <Text role="caption" weight="semibold" tone={v.text} numberOfLines={1} compact align="center">
                {label}
            </Text>
        </View>
    );
}
