// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { GaiaApiError } from '@/api/errors';
import { useUndoSettings, useUpdateUndoSettings } from '@/data';
import { describeError, describeFailureWith, useT } from '@/i18n/useT';
import { Button, Disclosure, Ltr, Notice, Stack, Text, TextField } from '@/ui';

import { isPcRefusal, PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';

function isWholeInRange(value: string, min: number, max: number): boolean {
    if (!/^\d+$/.test(value.trim())) return false;
    const n = Number(value);
    return n >= min && n <= max;
}

function formatBytes(bytes: number): string {
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** How long, and how much, GAIA keeps the copies that make "Undo this turn" work. */
export function PrivacyUndo() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const undo = useUndoSettings();
    const save = useUpdateUndoSettings();
    const [days, setDays] = useState<string | null>(null);
    const [mb, setMb] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);
    const [rejected, setRejected] = useState(false);
    const settings = undo.data;

    const daysText = days ?? (settings ? String(settings.retention_days) : '');
    const mbText = mb ?? (settings ? String(settings.max_mb) : '');
    const daysInvalid = !isWholeInRange(daysText, 1, 365);
    const mbInvalid = !isWholeInRange(mbText, 10, 100000);
    const dirty = !!settings && (daysText !== String(settings.retention_days) || mbText !== String(settings.max_mb));

    const submit = () => {
        setSaved(false);
        setRejected(false);
        save.mutate(
            { retentionDays: Number(daysText), maxMb: Number(mbText) },
            {
                onSuccess: () => {
                    setDays(null);
                    setMb(null);
                    setSaved(true);
                },
                onError: (error) => setRejected(error instanceof GaiaApiError && error.status === 422),
            },
        );
    };

    return (
        <SettingsAnchor name="undo" onTarget={() => setOpen(true)}>
            <Disclosure title={t('settings.privacy.undo')} open={open} onToggle={() => setOpen((o) => !o)} headerTestID="privacy-undo-toggle">
                <Stack gap="md">
                    <Text role="label" tone="muted">
                        {t('settings.undo.hint')}
                    </Text>
                    {isPcRefusal(undo.error) || isPcRefusal(save.error) ? (
                        <PcOnlyNotice testID="undo-pc-only" />
                    ) : undo.error ? (
                        <Notice tone="danger" message={t.compose((say) => say('settings.undo.readFailed', { reason: describeFailureWith(undo.error, say) }))} testID="undo-read-error" />
                    ) : null}
                    {settings ? (
                        <>
                            <TextField
                                label={t('settings.undo.days')}
                                value={daysText}
                                onChangeText={(v) => {
                                    setDays(v);
                                    setSaved(false);
                                    setRejected(false);
                                }}
                                keyboardType="number-pad"
                                error={daysInvalid ? t('settings.undo.daysError') : undefined}
                                testID="undo-days"
                            />
                            <TextField
                                label={t('settings.undo.mb')}
                                value={mbText}
                                onChangeText={(v) => {
                                    setMb(v);
                                    setSaved(false);
                                    setRejected(false);
                                }}
                                keyboardType="number-pad"
                                error={mbInvalid ? t('settings.undo.mbError') : undefined}
                                testID="undo-mb"
                            />
                            <Stack gap="xs">
                                <Text role="label" weight="medium" tone="secondary">
                                    {t('settings.undo.inUse')}
                                </Text>
                                <Text role="label">{t.plural('settings.undo.used', settings.turns, { size: formatBytes(settings.used_bytes) })}</Text>
                                <Ltr>{settings.path}</Ltr>
                            </Stack>
                            <Button
                                variant="primary"
                                full
                                label={save.isPending ? t('settings.models.applying') : t('settings.advanced.save')}
                                disabled={!dirty || daysInvalid || mbInvalid || save.isPending}
                                onPress={submit}
                                testID="undo-save"
                            />
                            {saved ? <Notice tone="success" message={t('settings.undo.saved')} testID="undo-saved" /> : null}
                            {rejected ? <Notice tone="danger" message={t('settings.undo.fix')} testID="undo-rejected" /> : null}
                            {save.error && !rejected && !isPcRefusal(save.error) ? <Notice tone="danger" message={describeError(save.error, t)} testID="undo-error" /> : null}
                        </>
                    ) : null}
                </Stack>
            </Disclosure>
        </SettingsAnchor>
    );
}
