// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';

import { IconButton, Row, Stack, Text } from '@/ui';
import { ArrowLeft } from '@/ui/icons';

/** The settings header: back (when given), the title, and a badge beside the title, and actions at the end. */
export function SettingsHeader({ title, backLabel, onBack, titleEnd, end }: { title: string; backLabel?: string; onBack?: () => void; titleEnd?: ReactNode; end?: ReactNode }) {
    return (
        <Stack paddingX="lg" paddingY="sm">
            <Row gap="sm" accessibilityRole="header">
                {onBack && backLabel ? <IconButton icon={ArrowLeft} label={backLabel} onPress={onBack} /> : null}
                <Row gap="sm" flex={1}>
                    <Stack>
                        <Text role="title" weight="semibold" numberOfLines={1}>{title}</Text>
                    </Stack>
                    {titleEnd}
                </Row>
                {end}
            </Row>
        </Stack>
    );
}
