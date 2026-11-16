// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Theme } from './ThemeProvider';

export type Tone =
    'primary' | 'secondary' | 'muted' | 'inverse' | 'accent' | 'danger' | 'success' | 'warning' | 'onAccent';

export function toneColor(colors: Theme['colors'], tone: Tone): string {
    switch (tone) {
        case 'primary':
            return colors.textPrimary;
        case 'secondary':
            return colors.textSecondary;
        case 'muted':
            return colors.textMuted;
        case 'inverse':
            return colors.textInverse;
        case 'accent':
            return colors.accent;
        case 'danger':
            return colors.danger;
        case 'success':
            return colors.accentGreen;
        case 'warning':
            return colors.accentYellow;
        case 'onAccent':
            return colors.accentFillText;
    }
}
