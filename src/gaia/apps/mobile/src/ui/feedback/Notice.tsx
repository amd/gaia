// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import { useT } from '@/i18n/useT';
import { Button } from '../controls/Button';
import { Icon } from '../controls/Icon';
import { IconButton } from '../controls/IconButton';
import { Check, CircleAlert, Info, TriangleAlert, X } from '../icons';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import type { Tone } from '../theme/tone';

export type NoticeTone = 'info' | 'warning' | 'danger' | 'success';

export interface NoticeProps {
    tone: NoticeTone;
    title?: string;
    message?: string;
    action?: { label: string; onPress: () => void };
    /** Shows a close button at the end. */
    onDismiss?: () => void;
    /** `note` or `status` marks advice that isn't an alert; a live region stays polite. */
    role?: 'note' | 'status';
    /** `card` is the desktop's `.model-warning`: a toned title and wider padding; both variants share the tinted box with a 25% border. */
    variant?: 'tint' | 'card';
    testID?: string;
}

const ICONS = { info: Info, warning: TriangleAlert, danger: CircleAlert, success: Check } as const;
const ICON_TONE: Record<NoticeTone, Tone> = { info: 'accent', warning: 'warning', danger: 'danger', success: 'success' };

function withAlpha(hex: string, alpha: number): string {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) throw new Error(`Notice: expected a #rrggbb color, got ${hex}`);
    return `rgba(${parseInt(m[1]!, 16)}, ${parseInt(m[2]!, 16)}, ${parseInt(m[3]!, 16)}, ${alpha})`;
}

// The icon carries the tone with the color, so it never rests on color alone.
export function Notice({ tone, title, message, action, onDismiss, role, variant = 'tint', testID }: NoticeProps) {
    const t = useT();
    const { colors, radius, space } = useTheme();
    const bar = { info: colors.accentSolid, warning: colors.accentYellow, danger: colors.danger, success: colors.accentGreen }[tone];
    const bg = { info: colors.accentDim2, warning: colors.accentYellowDim, danger: colors.dangerDim, success: colors.accentGreenDim }[tone];
    const card = variant === 'card';
    // On iOS an accessible ancestor swallows its children, so with buttons only the text block is one element.
    const hasControls = !!action || !!onDismiss;
    const semantics = {
        accessible: true,
        role,
        accessibilityRole: role ? undefined : tone === 'danger' || tone === 'warning' ? ('alert' as const) : undefined,
        accessibilityLiveRegion: !role && (tone === 'danger' || tone === 'warning') ? ('assertive' as const) : ('polite' as const),
    };
    return (
        <View
            testID={testID}
            {...(hasControls ? {} : semantics)}
            style={{
                backgroundColor: bg,
                borderRadius: radius.md,
                borderWidth: 1,
                borderColor: withAlpha(bar, 0.25),
                ...(card ? { paddingHorizontal: 14, paddingVertical: space.md } : { padding: space.md }),
            }}
        >
            <Row gap="md" align="start">
                <Icon icon={ICONS[tone]} size="md" tone={ICON_TONE[tone]} />
                <View style={{ flex: 1, minWidth: 0, gap: space.xs }}>
                    <View {...(hasControls ? semantics : {})} style={{ gap: space.xs }}>
                        {title ? (
                            <Text role="ui" weight="semibold" tone={card ? ICON_TONE[tone] : undefined}>
                                {title}
                            </Text>
                        ) : null}
                        {message ? (
                            <Text role="label" tone="secondary">
                                {message}
                            </Text>
                        ) : null}
                    </View>
                    {action ? <Button variant="secondary" label={action.label} onPress={action.onPress} /> : null}
                </View>
                {onDismiss ? <IconButton icon={X} size="sm" label={t('banner.dismiss')} onPress={onDismiss} /> : null}
            </Row>
        </View>
    );
}
