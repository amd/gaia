// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ENDPOINTS } from '@/api/constants';
import { useDeviceRefused } from '@/api/routeAccess';
import { useMemoryMaintenance, useMemorySettings, useRefreshMemory, type MemoryMaintenanceAction } from '@/data';
import { useT } from '@/i18n/useT';
import { ActionSheet, Badge, IconButton, Notice, Row, Screen, Stack, Text, type SheetAction } from '@/ui';
import { ArrowLeft, Ellipsis, RefreshCw } from '@/ui/icons';

import { failureNotice, MAINTENANCE_ACTIONS, MAINTENANCE_LABELS, outcomeNotice } from './maintenance';
import { MemoryTabs } from './MemoryTabs';
import { MEMORY_TABS, type MemoryTabKey } from './tabs';
import { useMemoryNotice } from './useMemoryNotice';

export interface MemoryScreenProps {
    onBack: () => void;
    /** Opens a chat from the conversation history. */
    onOpenChat?: (sessionId: string) => void;
    /** The tab to open on; the dev gallery uses it. */
    initialTab?: MemoryTabKey;
}

export function MemoryScreen({ onBack, onOpenChat, initialTab = 'about' }: MemoryScreenProps) {
    const t = useT();
    const [tab, setTab] = useState<MemoryTabKey>(initialTab);
    const [menuOpen, setMenuOpen] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const { notice, notify, dismiss } = useMemoryNotice();
    const settings = useMemorySettings();
    const refresh = useRefreshMemory();
    const maintenance = useMemoryMaintenance();
    const systemContextRefused = useDeviceRefused('POST', ENDPOINTS.memoryRefreshSystemContext);
    const running = maintenance.isPending ? maintenance.variables : undefined;

    const pullToRefresh = async () => {
        setRefreshing(true);
        try {
            await refresh();
        } finally {
            setRefreshing(false);
        }
    };

    const run = (action: MemoryMaintenanceAction) => {
        if (running) return;
        notify({ tone: 'info', text: t(MAINTENANCE_LABELS[action].busy), sticky: true });
        maintenance.mutate(action, {
            onSuccess: (outcome) => notify(outcomeNotice(outcome, t)),
            onError: (error) => notify(failureNotice(action, error, t)),
        });
    };

    const actions: SheetAction[] = [
        { label: t('mem.refresh'), onPress: () => void pullToRefresh() },
        ...MAINTENANCE_ACTIONS.map((action) => ({
            label: t(MAINTENANCE_LABELS[action][running === action ? 'busy' : 'idle']),
            disabled: action === 'refreshSystemContext' && systemContextRefused,
            onPress: () => run(action),
        })),
    ];
    const Pane = MEMORY_TABS.find((entry) => entry.key === tab)!.Pane;

    const header = (
        <Stack gap="sm" paddingY="sm">
            <Stack paddingX="lg">
                <Row gap="sm" accessibilityRole="header">
                    <IconButton icon={ArrowLeft} label={t('mem.back')} onPress={onBack} />
                    <Stack flex={1}>
                        <Row gap="sm" wrap>
                            <Text role="title" weight="semibold" accessibilityRole="header">
                                {t('mem.title')}
                            </Text>
                            <Badge label={t.bare('mem.beta')} accessibilityLabel={t('mem.beta')} tone="warning" />
                        </Row>
                    </Stack>
                    <IconButton icon={RefreshCw} label={t('mem.refreshAria')} busy={refreshing} onPress={() => void pullToRefresh()} testID="memory-refresh" />
                    {tab === 'advanced' ? (
                        <IconButton icon={Ellipsis} label={t('mem.maint.label')} busy={!!running} onPress={() => setMenuOpen(true)} testID="memory-maintenance" />
                    ) : null}
                </Row>
            </Stack>
            <MemoryTabs value={tab} onChange={setTab} />
            {notice ? (
                <Stack paddingX="lg">
                    <Notice tone={notice.tone} message={notice.text} action={notice.action} onDismiss={dismiss} testID="memory-notice" />
                </Stack>
            ) : null}
        </Stack>
    );

    return (
        <Screen scroll refreshing={refreshing} onRefresh={() => void pullToRefresh()} header={header}>
            <ActionSheet open={menuOpen} onClose={() => setMenuOpen(false)} title={t('mem.maint.label')} actions={actions} />
            <Pane notify={notify} memoryEnabled={settings.data?.memory_enabled} onOpenAdvanced={() => setTab('advanced')} onOpenChat={onOpenChat} />
        </Screen>
    );
}
