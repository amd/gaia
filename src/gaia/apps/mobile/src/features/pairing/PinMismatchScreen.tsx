// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Button, Icon, Screen, Stack, Text } from '@/ui';
import { ShieldAlert } from '@/ui/icons';

/** The paired PC's certificate changed (it reset pairing): the only way on is to pair again, never to trust it. */
export function PinMismatchScreen({ onPairAgain, busy }: { onPairAgain: () => void; busy?: boolean }) {
    const t = useT();
    return (
        <Screen edges={['top', 'bottom', 'start', 'end']}>
            <Stack flex={1} justify="center" gap="lg" padding="xl">
                <Icon icon={ShieldAlert} size="lg" tone="danger" />
                <Stack gap="sm">
                    <Text role="display" weight="semibold">{t('mobileApp.pairing.pinMismatch.title')}</Text>
                    <Text tone="secondary">{t('mobileApp.pairing.pinMismatch')}</Text>
                </Stack>
                <Button variant="primary" label={t('mobileApp.pairing.pinMismatch.action')} onPress={onPairAgain} busy={busy} full />
            </Stack>
        </Screen>
    );
}
