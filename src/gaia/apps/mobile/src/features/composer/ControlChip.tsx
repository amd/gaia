// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LucideIcon } from 'lucide-react-native';
import { useState } from 'react';
import { View } from 'react-native';

import { Icon, Pressable, Row, Text } from '@/ui';
import { ChevronDown } from '@/ui/icons';
import { a11yVisibility } from '@/ui/theme/a11y';

import { ChipMeasure, useChipCollapsed } from './ChipFitContext';
import { MODEL_FLOOR_WIDTH, type ChipId } from './chipRoom';

export interface ControlChipProps {
    label: string;
    icon: LucideIcon;
    onPress?: () => void;
    /** A chevron after the label marks a chip that opens a menu. */
    menu?: boolean;
    warning?: boolean;
    /** Which chip this is, so the row can drop its label when space runs out; the model's never goes. */
    chip: ChipId;
    disabled?: boolean;
    accessibilityLabel?: string;
    accessibilityHint?: string;
    testID?: string;
}

const MODEL_CHIP_MIN = 44;

/** A flat control in the message box's bottom row, as the desktop's composer chips: an icon, a short label and a chevron. */
export function ControlChip({ label, icon, onPress, menu, warning, chip, disabled, accessibilityLabel, accessibilityHint, testID }: ControlChipProps) {
    const collapsed = useChipCollapsed(chip);
    const [modelWidth, setModelWidth] = useState<number | null>(null);
    // Below the floor the model shows icon and chevron, never a sliver of its name.
    const belowFloor = chip === 'model' && modelWidth !== null && modelWidth < MODEL_FLOOR_WIDTH;
    const iconOnly = collapsed || belowFloor;
    const content = (compact: boolean) => (
        <Row gap="xs">
            <View style={{ flexShrink: 0 }}>
                <Icon icon={icon} size="sm" tone={warning ? 'warning' : 'secondary'} />
            </View>
            {compact ? null : (
                <View style={{ flexShrink: chip === 'model' ? 1 : 0 }}>
                    <Text role="label" tone={warning ? 'warning' : 'secondary'} numberOfLines={1} compact>
                        {label}
                    </Text>
                </View>
            )}
            {menu && !(compact && !belowFloor) ? (
                <View style={{ flexShrink: 0 }}>
                    <Icon icon={ChevronDown} size="sm" tone="muted" />
                </View>
            ) : null}
        </Row>
    );
    return (
        <View
            style={{ flexShrink: chip === 'model' ? 1 : 0, minWidth: chip === 'model' ? MODEL_CHIP_MIN : 0 }}
            onLayout={chip === 'model' ? (e) => setModelWidth(e.nativeEvent.layout.width) : undefined}
        >
            <ChipMeasure id={chip}>{content(false)}</ChipMeasure>
            {belowFloor ? (
                // Keeps the box sized by the full chip, so it widens again when the row has room.
                <View
                    {...a11yVisibility({ accessibilityElementsHidden: true, importantForAccessibility: 'no-hide-descendants' })}
                    pointerEvents="none"
                    style={{ height: 0, overflow: 'hidden' }}
                >
                    {content(false)}
                </View>
            ) : null}
            <Pressable
                onPress={onPress}
                disabled={disabled}
                accessibilityLabel={accessibilityLabel ?? label}
                accessibilityHint={accessibilityHint}
                accessibilityState={menu ? { expanded: false } : undefined}
                minSize={44}
                testID={testID}
            >
                {content(iconOnly)}
            </Pressable>
        </View>
    );
}
