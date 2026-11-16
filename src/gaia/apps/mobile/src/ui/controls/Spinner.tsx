// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ActivityIndicator } from 'react-native';
import { useTheme } from '../theme/ThemeProvider';
import { toneColor, type Tone } from '../theme/tone';

export function Spinner({ size = 'md', tone = 'secondary' }: { size?: 'sm' | 'md'; tone?: Tone }) {
    const { colors } = useTheme();
    return <ActivityIndicator size={size === 'sm' ? 'small' : 'large'} color={toneColor(colors, tone)} />;
}
