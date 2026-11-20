// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View } from 'react-native';
import { Icon } from '../controls/Icon';
import { Pressable } from '../controls/Pressable';
import { ChoiceList, type ChoiceOption } from '../feedback/ChoiceList';
import { ChevronDown } from '../icons';
import { Row } from '../layout/layout';
import { Sheet } from '../overlays/Sheet';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';

export interface SelectProps {
    label: string;
    /** Keeps the label for screen readers only, for a select whose purpose is plain from context. */
    hideLabel?: boolean;
    value?: string;
    options: ChoiceOption[];
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    testID?: string;
}

export function Select({ label, hideLabel, value, options, onChange, placeholder, disabled, testID }: SelectProps) {
    const { colors, radius, space } = useTheme();
    const [open, setOpen] = useState(false);
    const current = options.find((o) => o.value === value);
    const shown = current?.label ?? placeholder ?? '';
    return (
        <View style={{ gap: 6 }}>
            {hideLabel ? null : (
                <Text role="label" weight="medium" tone="secondary">
                    {label}
                </Text>
            )}
            <Pressable
                onPress={() => setOpen(true)}
                disabled={disabled}
                accessibilityLabel={`${label}: ${shown}`}
                accessibilityState={{ expanded: open }}
                testID={testID}
            >
                <View
                    style={{
                        minHeight: 48,
                        paddingHorizontal: space.md,
                        paddingVertical: space.sm,
                        borderRadius: radius.md,
                        borderWidth: 1,
                        borderColor: colors.border,
                        backgroundColor: colors.bgInput,
                        justifyContent: 'center',
                    }}
                >
                    <Row gap="sm">
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Text role="body" tone={current ? 'primary' : 'muted'} numberOfLines={2}>
                                {shown}
                            </Text>
                        </View>
                        <Icon icon={ChevronDown} size="sm" tone="muted" />
                    </Row>
                </View>
            </Pressable>
            <Sheet open={open} onClose={() => setOpen(false)} title={label}>
                <ChoiceList
                    options={options}
                    value={value}
                    onChange={(v) => {
                        onChange(v);
                        setOpen(false);
                    }}
                />
            </Sheet>
        </View>
    );
}
