// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import type { DrawerNavigationProp } from 'expo-router/drawer';
import { useNavigation } from 'expo-router';
import { useWindowDimensions } from 'react-native';

import { useT } from '@/i18n/useT';
import { IconButton, Row, Stack, Text } from '@/ui';
import { Menu } from '@/ui/icons';

import { PERMANENT_DRAWER_MIN_WIDTH } from './navigation';

export function useIsPermanentDrawer(): boolean {
    return useWindowDimensions().width >= PERMANENT_DRAWER_MIN_WIDTH;
}

/**
 * The page header: the hamburger that opens the chat drawer (hidden while it is permanent), then the title.
 * `titleNode` replaces the title text; `end` holds actions at the reading end.
 */
export function ScreenHeader({ title, titleNode, end }: { title?: string; titleNode?: ReactNode; end?: ReactNode }) {
    const t = useT();
    const navigation = useNavigation<DrawerNavigationProp<Record<string, object | undefined>>>();
    const permanent = useIsPermanentDrawer();
    return (
        <Stack paddingX="lg" paddingY="sm">
            <Row gap="sm" accessibilityRole="header">
                {permanent ? null : <IconButton icon={Menu} variant="raised" label={t('sidebar.open')} onPress={() => navigation.openDrawer()} />}
                {titleNode ? (
                    <Stack flex={1}>{titleNode}</Stack>
                ) : title ? (
                    <Stack flex={1}>
                        <Text role="title" weight="semibold" numberOfLines={1}>{title}</Text>
                    </Stack>
                ) : (
                    <Stack flex={end ? 1 : undefined} />
                )}
                {end}
            </Row>
        </Stack>
    );
}
