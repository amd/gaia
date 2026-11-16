// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isConnectAccountPayload } from '@webui/utils/connectAccountCard.core';

import { useT } from '@/i18n/useT';
import { Icon, Row, Stack, Surface, Text } from '@/ui';
import { Link2 } from '@/ui/icons';

import { UnsupportedCard } from './UnsupportedCard';

/** Signing in to an account happens on the PC, so the phone says where to do it; the chat offers "Ask again" once it's done. */
export function ConnectAccountCard({ data }: { data: unknown }) {
    const t = useT();
    if (!isConnectAccountPayload(data)) return <UnsupportedCard variant="invalid" render="connect_account" data={data} />;
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-connect-account">
            <Stack gap="sm">
                <Row gap="sm" align="center">
                    <Icon icon={Link2} size="sm" tone="accent" />
                    <Stack flex={1}>
                        <Text role="label">{t('connectCard.prompt')}</Text>
                    </Stack>
                </Row>
                <Text role="caption" tone="secondary">
                    {t('mobileApp.connectCard.onPc')}
                </Text>
            </Stack>
        </Surface>
    );
}
