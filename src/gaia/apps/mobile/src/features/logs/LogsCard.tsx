// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { ENDPOINTS } from '@/api/constants';
import { useDeviceRefused } from '@/api/routeAccess';
import { useT } from '@/i18n/useT';
import { Button, Card, Divider, Stack, Text } from '@/ui';
import { Archive, ScrollText } from '@/ui/icons';

import { PcOnlyNotice } from '../settings/PcOnlyNotice';

import { PcOnlyAction } from './PcOnlyAction';

/** Settings > Advanced > Logs: the web's card, opening the phone's read-only logs screen. */
export function LogsCard() {
    const t = useT();
    const router = useRouter();
    const refused = useDeviceRefused('GET', ENDPOINTS.logs);
    return (
        <Card padding="lg">
            <Stack gap="sm">
                <Text role="ui" weight="semibold">{t('logs.title')}</Text>
                <Text role="caption" tone="muted">{t('logs.settingsHint')}</Text>
                {refused ? (
                    <PcOnlyNotice testID="advanced-logs-pc-only" />
                ) : (
                    <Button label={t('logs.view')} icon={ScrollText} onPress={() => router.push('/logs')} testID="advanced-view-logs" />
                )}
                {refused ? null : (
                    <>
                        <Divider />
                        <PcOnlyAction
                            label={t('logs.saveAll')}
                            icon={Archive}
                            note={t('setup.logs.reportNote')}
                            detail={t('mobileApp.pcOnly.logs')}
                            testID="advanced-save-all-logs"
                        />
                    </>
                )}
            </Stack>
        </Card>
    );
}
