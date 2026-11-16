// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { describeError, useT } from '@/i18n/useT';
import { Button, Stack, Text } from '@/ui';

/** One section couldn't load: say why and let the person retry just that read; one the PC refuses says to look there. */
export function SectionFailed({ error, onRetry }: { error: unknown; onRetry: () => void }) {
    const t = useT();
    if (isPcRefusal(error)) return <PcOnlyNotice testID="memory-section-pc-only" />;
    return (
        <Stack gap="sm" align="start" paddingY="sm">
            <Text tone="danger">{t('mobileApp.mem.sectionFailed')}</Text>
            <Text role="caption" tone="muted">
                {describeError(error, t)}
            </Text>
            <Button label={t('mem.retry')} variant="secondary" size="sm" onPress={onRetry} />
        </Stack>
    );
}
