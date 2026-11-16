// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { DEFAULT_ACTIVITY_DAYS, UPCOMING_DAYS } from '@webui/utils/memoryFormat.core';

import type { MemoryStats } from '@/api/client.types';
import { useMemoryActivity, useMemoryTools, useMemoryUpcoming } from '@/data';
import { Spinner, Stack } from '@/ui';

import type { MemoryPaneProps } from '../tabs';

import { ConversationBrowser } from './conversations/ConversationBrowser';
import { KnowledgeBrowser } from './knowledge/KnowledgeBrowser';
import { ActivityChart } from './ActivityChart';
import { FooterStats } from './FooterStats';
import { SectionFailed } from './SectionFailed';
import { StatCards } from './StatCards';
import { ToolPerformance } from './ToolPerformance';
import { UpcomingList } from './UpcomingList';

/** The Advanced tab's content once the stats are in, in the web's order. Each section loads on its own and fails on its own. */
export function AdvancedBody({ stats, notify, onOpenChat }: { stats: MemoryStats } & Pick<MemoryPaneProps, 'notify' | 'onOpenChat'>) {
    const [days, setDays] = useState<number>(DEFAULT_ACTIVITY_DAYS);
    const activity = useMemoryActivity(days);
    const tools = useMemoryTools();
    const upcoming = useMemoryUpcoming(UPCOMING_DAYS);

    return (
        <Stack gap="xl" paddingX="lg" paddingY="lg" testID="memory-pane-advanced">
            <StatCards stats={stats} activity={activity.data ?? []} />
            <ActivityChart
                days={days}
                onDaysChange={setDays}
                activity={activity.data}
                failure={activity.isError && !activity.data ? { error: activity.error, onRetry: () => void activity.refetch() } : undefined}
            />
            {tools.data ? <ToolPerformance tools={tools.data} /> : tools.isError ? <SectionFailed error={tools.error} onRetry={() => void tools.refetch()} /> : <Spinner />}
            {upcoming.data ? <UpcomingList items={upcoming.data} /> : upcoming.isError ? <SectionFailed error={upcoming.error} onRetry={() => void upcoming.refetch()} /> : <Spinner />}
            <KnowledgeBrowser contexts={Object.keys(stats.knowledge.by_context)} notify={notify} />
            <ConversationBrowser onOpenChat={onOpenChat} />
            <FooterStats stats={stats} />
        </Stack>
    );
}
