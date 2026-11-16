// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Screen, Spinner, Stack, Text } from '@/ui';

/** "Starting GAIA…", or the embedded update's wait; shown until the setup check has answered. */
export function StartupScreen({ upgrading = false }: { upgrading?: boolean }) {
    const t = useT();
    return (
        <Screen>
            <Stack flex={1} gap="lg" align="center" justify="center" padding="xl">
                <Spinner />
                <Text role="body" tone="secondary" align="center">
                    {upgrading ? t('setup.upgrading.notice') : t('startup.starting')}
                </Text>
            </Stack>
        </Screen>
    );
}
