// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import { Row } from '../layout/layout';
import { Icon } from '../controls/Icon';
import { Check, CircleAlert, TriangleAlert } from '../icons';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import { Pulse } from './Pulse';

export type StatusTone = 'idle' | 'running' | 'attention' | 'error' | 'success';

export interface StatusDotProps {
    tone: StatusTone;
    /** The accessible name; shown beside the dot with `showLabel`. */
    label: string;
    showLabel?: boolean;
    /** A plain filled dot, as the desktop's, for success, attention and error; the label still names the state. */
    solid?: boolean;
}

const BOX = 16;

// Each tone has its own shape, so none of them depends on color.
export function StatusDot({ tone, label, showLabel, solid }: StatusDotProps) {
    const { colors } = useTheme();
    let mark;
    if (tone === 'idle') {
        mark = <View style={{ width: 10, height: 10, borderRadius: 5, borderWidth: 2, borderColor: colors.textMuted }} />;
    } else if (tone === 'running') {
        mark = (
            <Pulse>
                <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accentBlue }} />
            </Pulse>
        );
    } else if (solid) {
        const fill = tone === 'success' ? colors.accentGreen : tone === 'attention' ? colors.accentYellow : colors.danger;
        mark = <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: fill }} />;
    } else if (tone === 'success') {
        mark = <Icon icon={Check} size="sm" tone="success" />;
    } else if (tone === 'attention') {
        mark = <Icon icon={TriangleAlert} size="sm" tone="warning" />;
    } else {
        mark = <Icon icon={CircleAlert} size="sm" tone="danger" />;
    }
    return (
        <Row gap="xs">
            <View accessible accessibilityLabel={label} style={{ width: BOX, height: BOX, alignItems: 'center', justifyContent: 'center' }}>
                {mark}
            </View>
            {showLabel ? (
                <Text role="caption" tone="secondary" numberOfLines={1}>
                    {label}
                </Text>
            ) : null}
        </Row>
    );
}
