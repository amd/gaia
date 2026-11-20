// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LucideIcon } from 'lucide-react-native';
import { useDirection } from '@/i18n/useT';
import { useTheme } from '../theme/ThemeProvider';
import { a11yVisibility } from '../theme/a11y';
import { toneColor, type Tone } from '../theme/tone';

const SIZES = { sm: 16, md: 20, lg: 24 } as const;

// Icons that point along the reading direction mirror in right-to-left text (as in index.css).
const DIRECTIONAL = new Set([
    'ChevronRight',
    'ChevronLeft',
    'ArrowRight',
    'ArrowLeft',
    'PanelLeftClose',
    'PanelLeftOpen',
]);

export interface IconProps {
    icon: LucideIcon;
    size?: keyof typeof SIZES;
    tone?: Tone;
    label?: string;
}

export function Icon({ icon: Glyph, size = 'md', tone = 'secondary', label }: IconProps) {
    const { colors } = useTheme();
    const rtl = useDirection() === 'rtl';
    const mirror = rtl && DIRECTIONAL.has(Glyph.displayName ?? '');
    const px = SIZES[size];
    return (
        <Glyph
            size={px}
            color={toneColor(colors, tone)}
            accessibilityLabel={label}
            {...a11yVisibility({ accessible: !!label, importantForAccessibility: label ? 'yes' : 'no-hide-descendants' })}
            style={mirror ? { transform: [{ scaleX: -1 }] } : undefined}
        />
    );
}
