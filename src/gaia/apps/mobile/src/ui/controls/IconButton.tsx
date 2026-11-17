// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';
import { Icon } from './Icon';
import { Pressable, PRESSABLE_MIN_SIZE } from './Pressable';
import { Spinner } from './Spinner';
import { useTheme } from '../theme/ThemeProvider';
import type { Tone } from '../theme/tone';

export interface IconButtonProps {
    icon: LucideIcon;
    label: string;
    onPress?: () => void;
    variant?: 'ghost' | 'filled' | 'primary' | 'danger' | 'raised';
    size?: 'md' | 'sm';
    busy?: boolean;
    disabled?: boolean;
    testID?: string;
}

export function IconButton({
    icon,
    label,
    onPress,
    variant = 'ghost',
    size = 'md',
    busy,
    disabled,
    testID,
}: IconButtonProps) {
    const { colors, radius, shadows } = useTheme();
    const look: Record<NonNullable<IconButtonProps['variant']>, { bg: string; tone: Tone }> = {
        ghost: { bg: 'transparent', tone: 'secondary' },
        filled: { bg: colors.bgTertiary, tone: 'primary' },
        primary: { bg: colors.accentFill, tone: 'onAccent' },
        danger: { bg: colors.dangerDim, tone: 'danger' },
        raised: { bg: colors.bgSecondary, tone: 'primary' },
    };
    const v = look[variant];
    const box = size === 'sm' ? 36 : variant === 'raised' ? 38 : 44;
    return (
        <View style={{ minWidth: PRESSABLE_MIN_SIZE, minHeight: PRESSABLE_MIN_SIZE, alignItems: 'center', justifyContent: 'center' }}>
            <Pressable
                onPress={busy ? undefined : onPress}
                disabled={disabled}
                accessibilityLabel={label}
                accessibilityState={{ busy: !!busy }}
                testID={testID}
                round
            >
                <View
                    style={{
                        width: box,
                        height: box,
                        alignSelf: 'center',
                        borderRadius: radius.md,
                        backgroundColor: v.bg,
                        ...(variant === 'raised' ? { borderWidth: 1, borderColor: colors.border, boxShadow: shadows.sm } : null),
                        alignItems: 'center',
                        justifyContent: 'center',
                    }}
                >
                    {busy ? <Spinner size="sm" tone={v.tone} /> : <Icon icon={icon} tone={v.tone} />}
                </View>
            </Pressable>
        </View>
    );
}
