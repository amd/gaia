// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEnvironmentActions, useSessionEnvironment } from '@/data';
import { PcOnlyNotice, isPcRefusal } from '@/features/settings/PcOnlyNotice';
import { useT } from '@/i18n/useT';
import { Notice, Stack } from '@/ui';

import { failureReasonWith } from '../header/failureText';

/** Above the composer while the chat's internet is off, with a way back on. `live` re-reads when a reply ends. */
export function OfflineNotice({ sessionId, live = false }: { sessionId: string; live?: boolean }) {
    const t = useT();
    const { data: env } = useSessionEnvironment(sessionId, { enabled: true, live });
    const { setInternet } = useEnvironmentActions(sessionId);
    if (!env || env.internet) return null;
    if (isPcRefusal(setInternet.error)) {
        return (
            <Stack paddingX="lg">
                <PcOnlyNotice testID="offline-pc-only" />
            </Stack>
        );
    }
    const error = setInternet.error;
    return (
        <Stack paddingX="lg">
            <Notice
                tone="info"
                testID="offline-notice"
                message={
                    t('offline.text') +
                    (error ? ` ${t.compose((say) => say('offline.error', { error: failureReasonWith(error, say) }))}` : '')
                }
                action={{ label: t('offline.turnOn'), onPress: () => setInternet.mutate(true) }}
            />
        </Stack>
    );
}
