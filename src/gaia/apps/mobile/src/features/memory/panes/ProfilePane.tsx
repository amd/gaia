// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Button, LoadingState, Stack, Text } from '@/ui';

import { ProfileCard } from '../profile/ProfileCard';
import type { MemoryPaneProps } from '../tabs';

export function ProfilePane({ notify, memoryEnabled, onOpenAdvanced }: MemoryPaneProps) {
    const t = useT();
    if (memoryEnabled === undefined) return <LoadingState />;
    if (!memoryEnabled) {
        return (
            <Stack gap="md" paddingX="lg" paddingY="md" align="start" testID="memory-pane-profile">
                <Text tone="secondary">{t('mem.about.off')}</Text>
                <Button label={t.bare('mem.about.turnOn')} accessibilityLabel={t('mem.about.turnOn')} variant="secondary" onPress={onOpenAdvanced} />
            </Stack>
        );
    }
    return (
        <Stack gap="lg" paddingX="lg" paddingY="md" testID="memory-pane-profile">
            <Text tone="secondary">{t('mobileApp.mem.profile.intro')}</Text>
            <ProfileCard kind="discovery" notify={notify} />
            <ProfileCard kind="inference" notify={notify} />
        </Stack>
    );
}
