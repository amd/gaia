// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';
import { Text } from '../text/Text';
import { Icon } from './Icon';
import { Pressable, RIPPLE } from './Pressable';
import { Spinner } from './Spinner';
import { Row } from '../layout/layout';
import { useLargeText } from '../theme/a11y';
import { useTheme } from '../theme/ThemeProvider';
import type { Tone } from '../theme/tone';

export interface ButtonProps {
    label: string;
    onPress?: () => void;
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
    size?: 'md' | 'sm';
    icon?: LucideIcon;
    iconEnd?: LucideIcon;
    busy?: boolean;
    disabled?: boolean;
    full?: boolean;
    /** False keeps the button at its own width; set it beside a text that should wrap first. */
    shrink?: boolean;
    /** Pad a multi-line label, for a tall answer button; any label too wide for its row wraps rather than run off the screen. */
    wrap?: boolean;
    /** What a screen reader says instead of the visible label. */
    accessibilityLabel?: string;
    testID?: string;
}

/** A shrinking button keeps its icon and a short word. */
export const BUTTON_MIN_WIDTH = 88;

export function Button({
    label,
    onPress,
    variant = 'secondary',
    size = 'md',
    icon,
    iconEnd,
    busy,
    disabled,
    full,
    shrink = true,
    wrap,
    accessibilityLabel,
    testID,
}: ButtonProps) {
    const { colors, radius } = useTheme();
    const largeText = useLargeText();
    const look: Record<NonNullable<ButtonProps['variant']>, { bg: string; border: string; tone: Tone }> = {
        primary: { bg: colors.accentFill, border: 'transparent', tone: 'onAccent' },
        secondary: { bg: colors.bgTertiary, border: size === 'sm' ? colors.border : colors.borderLight, tone: 'primary' },
        ghost: { bg: 'transparent', border: colors.border, tone: 'accent' },
        danger: { bg: colors.dangerDim, border: 'transparent', tone: 'danger' },
    };
    const pressedBg = { primary: colors.accentSolid, secondary: colors.bgActive, ghost: colors.bgTertiary, danger: colors.dangerDim };
    const v = look[variant];
    return (
        <View
            style={[
                full ? { alignSelf: 'stretch' } : shrink ? { alignSelf: 'flex-start', flexShrink: 1, minWidth: BUTTON_MIN_WIDTH, maxWidth: '100%' } : { alignSelf: 'flex-start', flexShrink: 0 },
                // A regular button's shape fills its target, so the ripple is clipped here; a small one clips its own shape.
                RIPPLE && size !== 'sm' ? { borderRadius: radius.md, overflow: 'hidden' } : null,
            ]}
        >
            <Pressable
                onPress={busy ? undefined : onPress}
                disabled={disabled}
                minSize={size === 'sm' ? 44 : undefined}
                shape={size === 'sm' ? { radius: radius.sm } : undefined}
                accessibilityLabel={accessibilityLabel ?? label}
                accessibilityState={{ busy: !!busy }}
                testID={testID}
            >
                {({ highlight }) => (
                <View
                    style={{
                        minHeight: size === 'sm' ? 28 : 48,
                        paddingHorizontal: size === 'sm' ? 10 : 16,
                        borderRadius: size === 'sm' ? radius.sm : radius.md,
                        borderWidth: 1,
                        borderColor: v.border,
                        backgroundColor: highlight ? pressedBg[variant] : v.bg,
                        alignItems: 'center',
                        justifyContent: 'center',
                    }}
                >
                    <Row gap="sm" justify="center">
                        {busy ? (
                            <Spinner size="sm" tone={v.tone} />
                        ) : icon ? (
                            <Icon icon={icon} size="sm" tone={v.tone} />
                        ) : null}
                        <View style={{ flexShrink: 1, minWidth: 0, paddingVertical: wrap || largeText ? 10 : size === 'sm' ? 2 : 4 }}>
                            <Text role={size === 'sm' ? 'caption' : 'ui'} weight="medium" tone={v.tone} align="center" glossInline>
                                {label}
                            </Text>
                        </View>
                        {iconEnd && !busy ? <Icon icon={iconEnd} size="sm" tone={v.tone} /> : null}
                    </Row>
                </View>
                )}
            </Pressable>
        </View>
    );
}
