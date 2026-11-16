// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemorySettings, useMemoryStats } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { describeError, useT } from '@/i18n/useT';
import { EmptyState, ErrorState, LoadingState, Stack } from '@/ui';
import { Brain } from '@/ui/icons';

import { AdvancedBody } from '../advanced/AdvancedBody';
import { MemoryAdmin } from '../advanced/MemoryAdmin';
import { CoveragePill } from '../CoveragePill';
import type { MemoryPaneProps } from '../tabs';

/** The Advanced tab: the load and failed states, "Memory is off", then the stats, activity, tools, upcoming items, the knowledge browser and the conversation history. */
export function AdvancedPane({ notify, onOpenChat }: MemoryPaneProps) {
    const t = useT();
    const stats = useMemoryStats();
    const settings = useMemorySettings();

    const failed = (stats.isError && !stats.data) || (settings.isError && !settings.data);
    const loading = !failed && (!stats.data || !settings.data);
    const retry = () => {
        if (stats.isError) void stats.refetch();
        if (settings.isError) void settings.refetch();
    };

    return (
        <Stack flex={1}>
            <CoveragePill />
            {loading ? (
                <LoadingState title={t('mem.loadingData')} />
            ) : failed && isPcRefusal(stats.error ?? settings.error) ? (
                <Stack paddingX="lg" paddingY="md">
                    <PcOnlyNotice testID="memory-advanced-pc-only" />
                </Stack>
            ) : failed ? (
                <ErrorState
                    title={t('mem.loadFailed')}
                    message={describeError(stats.error ?? settings.error, t)}
                    action={{ label: t('mem.retry'), onPress: retry }}
                />
            ) : settings.data && !settings.data.memory_enabled ? (
                <EmptyState icon={Brain} title={t('mem.offAdvanced')} />
            ) : (
                stats.data ? <AdvancedBody stats={stats.data} notify={notify} onOpenChat={onOpenChat} /> : null
            )}
            {!loading && !failed && settings.data ? (
                <Stack paddingX="lg" paddingY="md">
                    <MemoryAdmin settings={settings.data} notify={notify} />
                </Stack>
            ) : null}
        </Stack>
    );
}
