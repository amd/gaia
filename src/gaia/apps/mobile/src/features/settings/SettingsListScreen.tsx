// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { useT } from '@/i18n/useT';
import { Icon, IconButton, ListRow, Screen } from '@/ui';
import { ChevronRight, Monitor, X } from '@/ui/icons';

import { openSettings } from './openSettings';
import { SECTION_ORDER, SETTINGS_SECTIONS } from './sections';
import { SettingsHeader } from './SettingsHeader';

export function SettingsListScreen() {
    const t = useT();
    const router = useRouter();
    const close = () => (router.canGoBack() ? router.back() : router.replace('/'));
    return (
        <Screen
            scroll
            header={<SettingsHeader title={t('settings.title')} end={<IconButton icon={X} label={t('settings.close')} onPress={close} />} />}
        >
            {SECTION_ORDER.map((id) => {
                const { titleKey, icon } = SETTINGS_SECTIONS[id];
                return (
                    <ListRow
                        key={id}
                        testID={`settings-section-${id}`}
                        title={t(titleKey)}
                        leading={<Icon icon={icon} tone="secondary" />}
                        trailing={<Icon icon={ChevronRight} size="sm" tone="muted" />}
                        onPress={() => openSettings(id)}
                    />
                );
            })}
            <ListRow
                testID="settings-paired-pcs"
                title={t('mobileApp.pairing.pcs.title')}
                leading={<Icon icon={Monitor} tone="secondary" />}
                trailing={<Icon icon={ChevronRight} size="sm" tone="muted" />}
                onPress={() => router.push('/pair/pcs')}
            />
        </Screen>
    );
}
