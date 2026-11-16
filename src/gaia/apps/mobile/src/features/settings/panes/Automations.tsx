// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { usePinned } from '@/api/routeAccess';
import { useT } from '@/i18n/useT';
import { Notice, Stack, Text } from '@/ui';

import { PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';

// Every /api/automations route is PC-only, so the phone explains instead of listing.
export default function Automations() {
    const t = useT();
    const pinned = usePinned();
    return (
        <SettingsAnchor name="pane">
            <Stack gap="md" padding="lg">
                <Text role="ui" tone="secondary">{t('settings.automations.lede')}</Text>
                {pinned ? (
                    <PcOnlyNotice testID="automations-pc-only" />
                ) : (
                    <Notice tone="info" message={t('mobileApp.settings.automations.body')} testID="automations-pc-only" />
                )}
            </Stack>
        </SettingsAnchor>
    );
}
