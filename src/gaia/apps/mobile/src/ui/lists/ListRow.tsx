// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { View } from 'react-native';
import { Pressable } from '../controls/Pressable';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import type { Tone } from '../theme/tone';

export interface ListRowProps {
    title: string;
    subtitle?: string;
    /** `danger` for a failed state such as "1 not sent". */
    subtitleTone?: Tone;
    leading?: ReactNode;
    trailing?: ReactNode;
    onPress?: () => void;
    onLongPress?: () => void;
    selected?: boolean;
    titleLines?: number;
    /** Let a long subtitle wrap to this many lines instead of truncating. */
    subtitleLines?: number;
    /** Semibold title, e.g. an unread chat. */
    emphasized?: boolean;
    accessibilityLabel?: string;
    testID?: string;
}

export function ListRow({
    title,
    subtitle,
    subtitleTone = 'muted',
    leading,
    trailing,
    onPress,
    onLongPress,
    selected,
    titleLines = 1,
    subtitleLines = 1,
    emphasized,
    accessibilityLabel,
    testID,
}: ListRowProps) {
    const { colors, space } = useTheme();
    const body = (
        <View
            style={{
                minHeight: 48,
                paddingHorizontal: space.lg,
                paddingVertical: space.sm,
                backgroundColor: selected ? colors.bgActive : 'transparent',
                justifyContent: 'center',
            }}
        >
            <Row gap="md">
                {leading}
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text role="ui" weight={selected || emphasized ? 'semibold' : 'medium'} numberOfLines={titleLines}>
                        {title}
                    </Text>
                    {subtitle ? (
                        <Text role="caption" tone={subtitleTone} numberOfLines={subtitleLines}>
                            {subtitle}
                        </Text>
                    ) : null}
                </View>
                {trailing ? <View style={{ flexShrink: 0 }}>{trailing}</View> : null}
            </Row>
        </View>
    );
    if (!onPress && !onLongPress) return <View testID={testID}>{body}</View>;
    return (
        <Pressable
            onPress={onPress}
            onLongPress={onLongPress}
            accessibilityLabel={accessibilityLabel}
            accessibilityState={{ selected: !!selected }}
            minSize={48}
            testID={testID}
        >
            {body}
        </Pressable>
    );
}
