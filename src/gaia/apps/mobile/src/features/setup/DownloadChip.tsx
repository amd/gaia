// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useSetupStatus } from '@/data';
import { useT } from '@/i18n/useT';
import { Icon, Pressable, Row, Text } from '@/ui';
import { CircleAlert, Download } from '@/ui/icons';

import { chipView } from './steps/downloadState';

/**
 * "Downloading 2 of 3 · 38%" while models download, or "A download failed" after one did; nothing otherwise.
 * `onPress` opens Settings > Model, where the downloads can be cancelled or resumed.
 */
export function DownloadChip({ onPress }: { onPress: () => void }) {
    const t = useT();
    const status = useSetupStatus();
    const view = chipView(status.data);
    if (!view) return null;
    const failed = view.kind === 'failed';
    const label = failed
        ? t('download.chipFailed')
        : t('download.chip', { done: String(view.done), count: String(view.count), pct: String(view.percent) });
    return (
        <Pressable onPress={onPress} accessibilityLabel={t('download.chipAria')} testID="download-chip">
            <Row gap="xs" align="center">
                <Icon icon={failed ? CircleAlert : Download} size="sm" tone={failed ? 'danger' : 'accent'} />
                <Text role="label" weight="medium" tone={failed ? 'danger' : 'primary'}>{label}</Text>
            </Row>
        </Pressable>
    );
}
