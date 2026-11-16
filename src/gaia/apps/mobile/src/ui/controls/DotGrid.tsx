// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { StyleSheet } from 'react-native';
import Svg, { Defs, Pattern, Rect } from 'react-native-svg';
import { useTheme } from '../theme/ThemeProvider';

// The desktop's new-chat backdrop at rest (webui npuTiles.ts): 2 px dots on a 30 px pitch.
const PITCH = 30;
const DOT = 2;

export function DotGrid() {
    const { colors, scheme } = useTheme();
    const mid = (PITCH - DOT) / 2;
    return (
        <Svg style={StyleSheet.absoluteFill} pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <Defs>
                <Pattern id="dot-grid" width={PITCH} height={PITCH} patternUnits="userSpaceOnUse">
                    <Rect x={mid} y={mid} width={DOT} height={DOT} fill={colors.textPrimary} fillOpacity={scheme === 'dark' ? 0.17 : 0.14} />
                </Pattern>
            </Defs>
            <Rect width="100%" height="100%" fill="url(#dot-grid)" />
        </Svg>
    );
}
