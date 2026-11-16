// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState, type ComponentRef } from 'react';
import { ScrollView, View } from 'react-native';

import { useT } from '@/i18n/useT';
import { Disclosure, Notice, Stack, Text, useTheme } from '@/ui';
import type { SetupLogStage, SetupRunStatus } from '@webui/types';

import { logsByStage } from './logLines';

const LOG_HEIGHT = 220;

/** A log that opens at its newest line and follows it until the reader scrolls up; scrolling back down follows again. */
function FollowLog({ lines }: { lines: string[] }) {
    const { colors, radius, space } = useTheme();
    const box = useRef<ComponentRef<typeof ScrollView>>(null);
    const pinned = useRef(true);
    return (
        <View
            style={{ height: LOG_HEIGHT, backgroundColor: colors.bgCard, borderRadius: radius.md, padding: space.sm }}>
            <ScrollView
                ref={box}
                nestedScrollEnabled
                onContentSizeChange={() => {
                    if (pinned.current) box.current?.scrollToEnd({ animated: false });
                }}
                onScroll={(e) => {
                    const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
                    pinned.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 8;
                }}
                scrollEventThrottle={64}
            >
                <Text mono role="caption" dir="ltr" selectable>
                    {lines.join('\n')}
                </Text>
            </ScrollView>
        </View>
    );
}

/**
 * "Having trouble?": each stage's log, behind one quiet toggle that opens itself when a step fails.
 * The troubleshooting report is the PC's to save, so a phone only says so and never asks for it.
 */
export function SetupLogs({
    run,
    before,
    failed = false,
}: {
    run: SetupRunStatus | null;
    before?: Partial<Record<SetupLogStage, string[]>>;
    failed?: boolean;
}) {
    const t = useT();
    const [help, setHelp] = useState(failed);
    const [open, setOpen] = useState<ReadonlySet<SetupLogStage>>(() => new Set());
    // A step that fails opens the help by itself, once.
    const [sawFailed, setSawFailed] = useState(failed);
    if (failed !== sawFailed) {
        setSawFailed(failed);
        if (failed) setHelp(true);
    }

    const toggle = (stage: SetupLogStage) =>
        setOpen((s) => {
            const next = new Set(s);
            if (next.has(stage)) next.delete(stage);
            else next.add(stage);
            return next;
        });

    return (
        <View accessibilityLabel={t('setup.logs.title')} testID="setup-logs">
            <Disclosure title={t('setup.logs.help')} open={help} onToggle={() => setHelp((h) => !h)}>
                <Stack gap="sm">
                    {logsByStage(run, before).map(({ stage, lines }) => (
                        <Disclosure key={stage} title={t(`setup.logs.${stage}`)} open={open.has(stage)} onToggle={() => toggle(stage)}>
                            <FollowLog lines={lines} />
                        </Disclosure>
                    ))}
                    <Text role="caption" tone="muted">
                        {t('setup.logs.reportNote')}
                    </Text>
                    <Notice tone="info" message={t('setup.logs.reportOnPc')} testID="setup-report-on-pc" />
                </Stack>
            </Disclosure>
        </View>
    );
}
