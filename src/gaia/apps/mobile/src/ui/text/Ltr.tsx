// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';
import { isolate } from '@webui/i18n/registry';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from './Text';

/** `chip` is the desktop's `.setting-path`: one mono line on a tertiary chip, cut in the middle. */
export function Ltr({ children, chip }: { children: string; chip?: boolean }) {
    const { colors, radius } = useTheme();
    if (!chip) return <Text>{isolate(children, 'ltr')}</Text>;
    return (
        <View
            style={{
                flexShrink: 1,
                minWidth: 0,
                backgroundColor: colors.bgTertiary,
                borderColor: colors.borderLight,
                borderWidth: 1,
                borderRadius: radius.sm,
                paddingHorizontal: 10,
                paddingVertical: 3,
            }}
        >
            <Text role="caption" mono tone="secondary" numberOfLines={1} ellipsize="middle" selectable>
                {isolate(children, 'ltr')}
            </Text>
        </View>
    );
}
