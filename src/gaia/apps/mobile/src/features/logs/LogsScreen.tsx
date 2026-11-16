// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Divider, Screen, Stack, Text } from '@/ui';
import { Archive, FileText } from '@/ui/icons';

import { SettingsHeader } from '../settings/SettingsHeader';
import { PcOnlyAction } from './PcOnlyAction';

/** The log viewer's place on the phone: GAIA's logs are read only on the PC, so every action explains that. */
export function LogsScreen({ onBack }: { onBack: () => void }) {
    const t = useT();
    return (
        <Screen scroll header={<SettingsHeader title={t('logs.title')} backLabel={t('setup.back')} onBack={onBack} />}>
            <Stack gap="lg" paddingX="lg" paddingY="md">
                <Text role="ui" tone="secondary">{t('logs.settingsHint')}</Text>
                <Text role="caption" tone="muted">{t('mobileApp.pcOnly.logs')}</Text>
                <Text role="caption" tone="muted">{t('logs.privacy')}</Text>
                <Divider />
                <PcOnlyAction
                    label={t('logs.saveAll')}
                    icon={Archive}
                    note={t('mobileApp.logs.reportNote')}
                    detail={t('mobileApp.pcOnly.logs')}
                    testID="logs-save-all"
                />
                <PcOnlyAction
                    label={t('setup.logs.report')}
                    icon={FileText}
                    detail={t('setup.logs.reportOnPc')}
                    testID="logs-report"
                />
            </Stack>
        </Screen>
    );
}
