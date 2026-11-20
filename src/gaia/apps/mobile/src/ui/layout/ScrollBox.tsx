// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { ScrollView } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';

export interface ScrollBoxProps {
    /** Height in points at which the content starts to scroll. */
    maxHeight: number;
    children?: ReactNode;
    testID?: string;
}

/** A bounded region that scrolls vertically (a tool step's output); never sideways, so long lines wrap unless the child is an `HScroll`. */
export function ScrollBox({ maxHeight, children, testID }: ScrollBoxProps) {
    const { colors, radius, space } = useTheme();
    return (
        <ScrollView
            nestedScrollEnabled
            showsVerticalScrollIndicator
            style={{
                maxHeight,
                borderRadius: radius.md,
                borderWidth: 1,
                borderColor: colors.border,
                backgroundColor: colors.bgSecondary,
            }}
            contentContainerStyle={{ padding: space.md }}
            testID={testID}
        >
            {children}
        </ScrollView>
    );
}
