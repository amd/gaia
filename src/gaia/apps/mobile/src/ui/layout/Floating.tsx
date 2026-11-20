// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { View } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';
import type { Space } from '../tokens/space';

export interface FloatingProps {
    children?: ReactNode;
    /** Only the bottom end for now. */
    edge?: 'bottom-end';
    /** Distance from both edges. */
    offset?: Space;
}

/** Floats over its parent; touches outside the child pass through. Aligns with flex-end so the reading end mirrors on native and web. */
export function Floating({ children, offset = 'md' }: FloatingProps) {
    const { space } = useTheme();
    const gap = space[offset];
    return (
        <View pointerEvents="box-none" style={{ position: 'absolute', left: gap, right: gap, bottom: gap, alignItems: 'flex-end' }}>
            {children}
        </View>
    );
}
