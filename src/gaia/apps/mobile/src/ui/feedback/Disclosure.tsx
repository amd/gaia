// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { View } from 'react-native';
import { Icon } from '../controls/Icon';
import { Pressable } from '../controls/Pressable';
import { ChevronDown, ChevronRight } from '../icons';
import { Row, Stack } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';

export interface DisclosureProps {
    title: string;
    open: boolean;
    onToggle: () => void;
    children?: ReactNode;
    /** Icon slot before the title; mirrors in Arabic. */
    leading?: ReactNode;
    trailing?: ReactNode;
    /** A line under the title, as the desktop's settings groups show. */
    hint?: string | null;
    /** A count pill at the end of the header. */
    count?: number;
    /** The desktop's settings group: a bordered box on the primary surface. */
    boxed?: boolean;
    testID?: string;
    /** Goes on the header press target, so a device flow can tap the toggle. */
    headerTestID?: string;
}

export function Disclosure({ title, open, onToggle, children, leading, trailing, hint, count, boxed, testID, headerTestID }: DisclosureProps) {
    const { colors, radius, space } = useTheme();
    const header = (
        <Pressable onPress={onToggle} accessibilityLabel={title} accessibilityState={{ expanded: open }} testID={headerTestID}>
            <View style={boxed ? { paddingHorizontal: space.md, paddingVertical: space.sm } : undefined}>
                <Row gap="sm" align={hint ? 'start' : 'center'}>
                    <Icon icon={open ? ChevronDown : ChevronRight} size="sm" tone="secondary" />
                    {leading}
                    <Stack flex={1} gap="xs">
                        <Text role="ui" weight="semibold" dir="auto" numberOfLines={hint ? undefined : 1}>
                            {title}
                        </Text>
                        {hint ? (
                            <Text role="label" tone="secondary">
                                {hint}
                            </Text>
                        ) : null}
                    </Stack>
                    {trailing}
                    {count !== undefined ? (
                        <View style={{ backgroundColor: colors.bgHover, borderRadius: radius.full, paddingHorizontal: 7, minWidth: 22 }}>
                            <Text role="caption" tone="secondary" align="center">
                                {String(count)}
                            </Text>
                        </View>
                    ) : null}
                </Row>
            </View>
        </Pressable>
    );
    if (boxed) {
        return (
            <View
                testID={testID}
                style={{ backgroundColor: colors.bgPrimary, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderLight }}
            >
                {header}
                {open ? <View style={{ paddingHorizontal: space.md, paddingBottom: space.sm, gap: space.sm }}>{children}</View> : null}
            </View>
        );
    }
    return (
        <View testID={testID}>
            {header}
            {open ? <View style={{ paddingStart: space.xl, paddingBottom: space.sm }}>{children}</View> : null}
        </View>
    );
}
