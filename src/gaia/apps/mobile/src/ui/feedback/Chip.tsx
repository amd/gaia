// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LucideIcon } from 'lucide-react-native';
import { View } from 'react-native';
import { Icon } from '../controls/Icon';
import { Pressable } from '../controls/Pressable';
import { Check } from '../icons';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';

export interface ChipProps {
    label: string;
    /** The full label a screen reader says when `label` is the bare one. */
    accessibilityLabel?: string;
    selected?: boolean;
    onPress?: () => void;
    icon?: LucideIcon;
    testID?: string;
}

// Selected shows a check beside the label as well as the accent fill.
export function Chip({ label, accessibilityLabel, selected, onPress, icon, testID }: ChipProps) {
    const { colors, radius } = useTheme();
    return (
        <Pressable onPress={onPress} accessibilityLabel={accessibilityLabel ?? label} accessibilityState={{ selected: !!selected }} testID={testID}>
            <View
                style={{
                    alignSelf: 'flex-start',
                    maxWidth: '100%',
                    minHeight: 32,
                    paddingHorizontal: 12,
                    justifyContent: 'center',
                    borderRadius: radius.full,
                    borderWidth: 1,
                    borderColor: selected ? colors.accentSolid : colors.borderLight,
                    backgroundColor: selected ? colors.accentDim : colors.bgCard,
                }}
            >
                <Row gap="xs">
                    {selected ? <Icon icon={Check} size="sm" tone="accent" /> : icon ? <Icon icon={icon} size="sm" tone="secondary" /> : null}
                    <View style={{ flexShrink: 1 }}>
                        <Text role="label" weight="medium" tone={selected ? 'accent' : 'primary'} numberOfLines={1} compact>
                            {label}
                        </Text>
                    </View>
                </Row>
            </View>
        </Pressable>
    );
}
