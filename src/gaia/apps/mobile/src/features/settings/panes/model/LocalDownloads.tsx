// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';

import { downloadsActive, isMoving, scopedStatus, sortedModels } from '@webui/components/setup/downloadModel';
import { overallFromPlan } from '@webui/components/setup/selection';
import type { SetupModelProgress } from '@webui/types';

import {
    useCancelSetup,
    useDownloadSetupModel,
    useRedownloadSetupModel,
    useSetupPlan,
    useSetupStatus,
    useSkipSetupModel,
} from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Card, ProgressLine, Row, Stack, Text } from '@/ui';

import { ModelRow } from '../../../setup/steps/DownloadRows';
import { useDownloadEstimates } from '../../../setup/steps/useDownloadEstimates';
import { useDownloadQueue } from './downloadQueue';
import { LocalError } from './LocalError';

/** Downloads moving or finished on the PC; renders nothing while there is none. */
export function LocalDownloads() {
    const t = useT();
    const status = useSetupStatus();
    const plan = useSetupPlan().data;
    const cancel = useCancelSetup();
    const download = useDownloadSetupModel();
    const skip = useSkipSetupModel();
    const redownload = useRedownloadSetupModel();
    const { queue, started, enqueue, shift, clear } = useDownloadQueue();
    const [error, setError] = useState<unknown>(null);

    const run = useMemo(() => scopedStatus(status.data ?? null, started), [status.data, started]);
    const moving = isMoving(status.data ?? null);

    // The PC pulls one model at a time: start the next queued one when it is free.
    useEffect(() => {
        if (moving || download.isPending || queue.length === 0) return;
        const next = shift();
        if (!next) return;
        download.mutate({ modelId: next.id, acknowledgeLowDisk: next.acknowledgeLowDisk }, { onError: setError });
    }, [moving, download, queue, shift]);

    const rows = useMemo(() => sortedModels((run?.models ?? []).filter((m) => m.state !== 'skipped')), [run]);
    const checking = rows.some((m) => m.state === 'verifying');
    const overall = useMemo(() => (run ? overallFromPlan(run, plan ?? null) : undefined), [run, plan]);
    const { estimates, now, checkingSeconds } = useDownloadEstimates(rows, overall, checking);

    if (!run || rows.length === 0) return null;

    const names: Record<string, string> = {};
    for (const e of plan?.catalog ?? []) names[e.id] = e.name;
    const active = downloadsActive(run) || queue.length > 0;
    const cancelled = rows.filter((m) => m.state === 'cancelled');
    const busy = [cancel, download, skip, redownload].some((m) => m.isPending);
    const act = (fn: () => Promise<unknown>) => {
        setError(null);
        fn().catch(setError);
    };
    const retry = (m: SetupModelProgress) =>
        act(async () => {
            if (m.error_kind === 'verify') await redownload.mutateAsync(m.id);
            else await download.mutateAsync({ modelId: m.id });
        });

    return (
        <Stack gap="sm" testID="local-downloads">
            <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">{t('settings.models.downloads')}</Text>
            <Card>
                <Stack gap="lg">
                    {rows.length > 1 || active ? <ProgressLine value={(overall?.percent ?? 0) / 100} /> : null}
                    {rows.map((m) => (
                        <ModelRow
                            key={m.id}
                            model={m}
                            name={names[m.id] ?? m.id}
                            estimate={estimates[m.id]}
                            now={now}
                            freeGb={plan?.disk?.free_gb ?? null}
                            checking={checking}
                            checkingFor={checkingSeconds(m.id)}
                            retryLater={moving}
                            idle={!active}
                            busy={busy}
                            onRetry={retry}
                            onResume={(row) => act(() => download.mutateAsync({ modelId: row.id }))}
                            onSkip={(row) => act(() => skip.mutateAsync({ modelId: row.id }))}
                            onRestart={() => act(async () => { await cancel.mutateAsync(); })}
                        />
                    ))}
                    <Row gap="sm" wrap>
                        {active ? (
                            <Button
                                label={t('setup.progress.cancel')}
                                variant="secondary"
                                size="sm"
                                disabled={busy}
                                onPress={() => act(async () => { clear(); await cancel.mutateAsync(); })}
                            />
                        ) : null}
                        {!active && cancelled.length > 0 ? (
                            <Button
                                label={t('setup.progress.resumeAll')}
                                variant="primary"
                                size="sm"
                                disabled={busy}
                                onPress={() => enqueue(cancelled.map((m) => m.id), false)}
                            />
                        ) : null}
                    </Row>
                </Stack>
            </Card>
            {error != null ? <LocalError error={error} /> : null}
        </Stack>
    );
}
