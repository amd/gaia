// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { GaiaApiError } from '@/api/errors';
import { useMemorySettings, useUpdateMemorySettings } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Card, Notice, SmallButton, Stack, Switch, Text } from '@/ui';

import { SettingsAnchor } from '../targets';

/** Memory on or off, and the way into the Memory screen. */
export default function Memory() {
    const t = useT();
    const router = useRouter();
    const settings = useMemorySettings();
    const update = useUpdateMemorySettings();
    const error = update.error ?? settings.error;
    const refused = error instanceof GaiaApiError && error.status === 403;

    return (
        <Stack gap="lg" padding="lg">
            <Text role="body" tone="secondary">
                {t('mobileApp.settings.memory.lede')}
            </Text>
            <SettingsAnchor name="remember">
                <Card>
                    <Stack gap="md">
                        <Switch
                            label={t('settings.memory.remember')}
                            value={settings.data?.memory_enabled ?? false}
                            disabled={!settings.data || update.isPending}
                            onValueChange={(on) => update.mutate({ memory_enabled: on })}
                            testID="memory-remember"
                        />
                        {refused ? (
                            <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="memory-pc-only" />
                        ) : error ? (
                            <Notice tone="danger" message={describeError(error, t)} testID="memory-error" />
                        ) : null}
                        <Stack gap="sm">
                            <Text role="label" tone="muted">
                                {t('settings.memory.dashboardHint')}
                            </Text>
                            <SmallButton variant="plain" label={t.bare('settings.memory.dashboard')} accessibilityLabel={t('settings.memory.dashboard')} onPress={() => router.push('/memory')} testID="memory-open-dashboard" />
                        </Stack>
                    </Stack>
                </Card>
            </SettingsAnchor>
        </Stack>
    );
}
