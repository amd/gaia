// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { formatSize, ltrIn } from '@webui/components/setup/format';

import { useGpuMemory } from '@/data';
import { useLocaleCode, useT } from '@/i18n/useT';
import { Disclosure, Notice, Stack, Text } from '@/ui';

import { SettingsAnchor } from '../../targets';
import { errorText } from './modelText';

/** GPU memory on a PC that shares it: today's split, read only. Changing it needs someone at the PC, so a phone only reads it. */
export function GpuMemory() {
    const t = useT();
    const locale = useLocaleCode();
    const read = useGpuMemory();
    const [picked, setPicked] = useState<boolean | null>(null);
    const status = read.data;
    const failed = read.error;

    if (!status?.applies && !failed) return null;
    const open = picked ?? (!!status?.pending || !!failed);
    const gb = (n: number) => ltrIn(locale, formatSize(n));

    return (
        <SettingsAnchor name="gpu-memory" onTarget={() => setPicked(true)}>
            <Disclosure title={t('gpu.settingsTitle')} open={open} onToggle={() => setPicked(!open)} headerTestID="gpu-memory-toggle" testID="gpu-memory">
                <Stack gap="sm">
                    {failed ? <Notice tone="danger" message={errorText(failed, t)} /> : null}
                    {status?.applies && status.total_gb != null && status.gpu_gb != null ? (
                        <>
                            <Text role="caption" tone="secondary">{t('mobileApp.gpu.body', { total: gb(status.total_gb), gpu: gb(status.gpu_gb) })}</Text>
                            {status.cpu_gb != null ? (
                                <Text role="caption" tone="muted">
                                    {`${t('gpu.split.gpu', { gb: gb(status.gpu_gb) })} · ${t('gpu.split.cpu', { gb: gb(status.cpu_gb) })}`}
                                </Text>
                            ) : null}
                            <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="gpu-memory-pc-only" />
                        </>
                    ) : null}
                </Stack>
            </Disclosure>
        </SettingsAnchor>
    );
}
