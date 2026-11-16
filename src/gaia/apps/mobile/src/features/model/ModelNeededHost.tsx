// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';

import { formatSize } from '@webui/components/setup/format';

import type { ModelNeed } from '@/api/client.types';
import { DOWNLOAD_MISS_LIMIT, DOWNLOAD_NO_ROW_LIMIT, DOWNLOAD_POLL_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { requireClient, useGaiaClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { useModelNeededStore } from '@/state/modelNeeded';
import { Button, Notice, ProgressLine, Row, Sheet, Spinner, Stack, Text } from '@/ui';

/**
 * The prompt for a job whose model isn't on the PC: names the model, its size and source, and downloads it
 * on the PC. The message that needed it waits on the answer. Mount once, inside the providers.
 */
export function ModelNeededHost() {
    const need = useModelNeededStore((s) => s.need);
    // A new prompt remounts, so a download and its errors never carry over from the last one.
    return need ? <NeedPrompt key={`${need.job}:${need.model?.id ?? need.off}`} need={need} /> : null;
}

function NeedPrompt({ need }: { need: ModelNeed }) {
    const t = useT();
    const client = useGaiaClient();
    const settle = useModelNeededStore((s) => s.settle);
    const timer = useRef<ReturnType<typeof setInterval> | null>(null);
    const noRow = useRef(0);
    const misses = useRef(0);
    // A closed or replaced prompt must not be answered by its download.
    const live = useRef(true);
    const [percent, setPercent] = useState<number | null>(null);
    const [downloading, setDownloading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [serverShort, setServerShort] = useState(false);

    const stop = useCallback(() => {
        if (timer.current) clearInterval(timer.current);
        timer.current = null;
    }, []);

    useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
            stop();
        };
    }, [stop]);

    const close = () => {
        stop();
        if (downloading && need.model && client) {
            client
                .cancelSetup(need.model.id)
                .catch((err) => console.warn('[ModelNeededHost] could not cancel the download', err));
        }
        settle(false);
    };

    const download = async (acknowledgeLowDisk: boolean) => {
        if (!need.model) return;
        const id = need.model.id;
        const c = requireClient(client);
        setError(null);
        setDownloading(true);
        noRow.current = 0;
        misses.current = 0;
        try {
            await c.downloadSetupModel(id, acknowledgeLowDisk);
        } catch (err) {
            if (!live.current) return;
            setDownloading(false);
            setError(err instanceof GaiaApiError ? err.detail : describeError(err, t));
            if (err instanceof GaiaApiError && err.status === 409) setServerShort(true);
            return;
        }
        if (!live.current) {
            c.cancelSetup(id).catch((err) => console.warn('[ModelNeededHost] could not cancel the download', err));
            return;
        }
        stop();
        timer.current = setInterval(() => {
            c.getSetupStatus()
                .then((status) => {
                    if (!live.current) return stop();
                    misses.current = 0;
                    const row = status.models?.find((m) => m.id === id);
                    if (!row) {
                        if (++noRow.current < DOWNLOAD_NO_ROW_LIMIT) return;
                        stop();
                        setDownloading(false);
                        setError(t('modelNeeded.failed'));
                        return;
                    }
                    if (row.bytes_total > 0) setPercent(Math.floor((row.bytes_done * 100) / row.bytes_total));
                    if (row.state === 'ready' || row.state === 'verifying') {
                        stop();
                        settle(true);
                    } else if (row.state === 'failed' || row.state === 'cancelled') {
                        stop();
                        setDownloading(false);
                        setError(row.error ?? t('modelNeeded.failed'));
                    }
                })
                .catch((err) => {
                    if (!live.current) return stop();
                    // One unanswered read isn't a lost download: it keeps going on the PC.
                    if (++misses.current < DOWNLOAD_MISS_LIMIT) return;
                    stop();
                    setDownloading(false);
                    setError(describeError(err, t));
                });
        }, DOWNLOAD_POLL_MS);
    };

    if (need.off) {
        return (
            <Sheet
                open
                onClose={close}
                title={t(`modelNeeded.off.${need.off}.title`)}
                footer={
                    <Stack gap="sm">
                        <Button full variant="primary" label={t('modelNeeded.off.send')} onPress={() => settle(true)} />
                        <Button full label={t('modelNeeded.notNow')} onPress={close} />
                    </Stack>
                }
            >
                <Text role="body">{t(`modelNeeded.off.${need.off}.body`)}</Text>
            </Sheet>
        );
    }
    if (!need.model) return null;
    const { model, job, disk_free_gb: freeGb } = need;
    // The same rule as setup's: more to download than is free.
    const tooBig = model.size_gb != null && freeGb != null && model.size_gb > freeGb;
    const short = tooBig || serverShort;
    return (
        <Sheet
            open
            onClose={close}
            title={t(`modelNeeded.title.${job}`)}
            footer={
                <Stack gap="sm">
                    <Button
                        full
                        variant="primary"
                        label={
                            short
                                ? t('setup.confirm.downloadAnyway')
                                : error
                                  ? t('setup.tryAgain')
                                  : t('modelNeeded.download')
                        }
                        disabled={downloading}
                        onPress={() => void download(short)}
                    />
                    <Button full label={t('modelNeeded.notNow')} onPress={close} />
                </Stack>
            }
        >
            <Stack gap="md">
                <Text role="body">
                    {[
                        model.name,
                        model.size_gb != null ? formatSize(model.size_gb) : null,
                        freeGb != null ? `${t('modelNeeded.free')} ${formatSize(freeGb)}` : null,
                    ]
                        .filter(Boolean)
                        .join(' · ')}
                </Text>
                {model.source ? (
                    <Text role="label" tone="secondary">
                        {`${t('setup.contents.from')} ${model.source}`}
                    </Text>
                ) : null}
                <Text role="label" tone="secondary">
                    {t('modelNeeded.waits')}
                </Text>
                {downloading ? (
                    percent != null ? (
                        <ProgressLine value={percent / 100} label={t('modelNeeded.dialog')} />
                    ) : (
                        <Row gap="sm">
                            <Spinner />
                            <Text role="label">{t('modelNeeded.starting')}</Text>
                        </Row>
                    )
                ) : null}
                {tooBig && !downloading ? (
                    <Notice
                        tone="danger"
                        message={t('modelNeeded.lowDisk', {
                            need: formatSize(model.size_gb ?? 0),
                            free: formatSize(freeGb ?? 0),
                            freeUp: formatSize((model.size_gb ?? 0) - (freeGb ?? 0)),
                        })}
                    />
                ) : null}
                {error ? <Notice tone="danger" message={error} /> : null}
            </Stack>
        </Sheet>
    );
}
