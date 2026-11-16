// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { connectedProviderLabel, referencedProviderConnected } from '@webui/utils/connectorRetry.core';

import { useConnectorWatch } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Notice, Stack } from '@/ui';

export interface ConnectorRetryProps {
    /** The newest reply: a connector sign-in error. */
    content: string;
    /** The connectors the reply's Connect card waits on; empty when it has none. */
    providers?: string[];
    /** Asks the last question again. */
    onRetry(): void;
}

/** Once the account a connector-error reply asked for is connected, offers to run that question again. */
export function ConnectorRetry({ content, providers = [], onRetry }: ConnectorRetryProps) {
    const t = useT();
    const [dismissed, setDismissed] = useState(false);
    const connectors = useConnectorWatch(true, (list) => referencedProviderConnected(content, list?.connectors ?? [], providers));
    const list = connectors.data?.connectors ?? [];
    if (dismissed || !referencedProviderConnected(content, list, providers)) return null;
    return (
        <Stack gap="xs" paddingX="lg" testID="connector-retry">
            <Notice
                tone="success"
                message={t('mobileApp.connectorRetry.connected', { name: connectedProviderLabel(content, list, providers) })}
                action={{
                    label: t('mobileApp.connectorRetry.askAgain'),
                    onPress: () => {
                        setDismissed(true);
                        onRetry();
                    },
                }}
            />
            <Button variant="ghost" size="sm" label={t('banner.dismiss')} onPress={() => setDismissed(true)} testID="connector-retry-dismiss" />
        </Stack>
    );
}
