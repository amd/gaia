// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';

import { useActiveModel, useRetryChoices, type RetryModel } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ListRow, Notice, Row, Sheet, Spinner, Stack, Text } from '@/ui';

export interface RetrySheetProps {
    open: boolean;
    onClose: () => void;
    /** The model that answered; it is left out of the list. */
    answeredBy?: string | null;
    onPick: (model: string) => void;
}

/** "Retry with…": pick another model to answer the same question. */
export function RetrySheet({ open, onClose, answeredBy, onPick }: RetrySheetProps) {
    const t = useT();
    const active = useActiveModel().data;
    const cloudName = active?.remote ? active.provider_name ?? null : null;
    const choices = useRetryChoices(open, active?.remote ? active.provider : null);
    const cloud = choices.data?.cloud.filter((m) => m.id !== answeredBy) ?? [];
    const local = choices.data?.local.filter((m) => m.id !== answeredBy) ?? [];

    const row = (m: RetryModel, hint: string) => (
        <ListRow
            key={m.id}
            title={m.name}
            subtitle={m.sizeGb ? isolate(`${m.sizeGb} GB`, 'ltr') : hint}
            onPress={() => {
                onClose();
                onPick(m.id);
            }}
        />
    );

    return (
        <Sheet open={open} onClose={onClose} title={t('retry.head')}>
            <Stack gap="sm">
                {choices.isError ? <Notice tone="danger" message={describeError(choices.error, t)} /> : null}
                {choices.isPending && !choices.isError ? <Row gap="sm"><Spinner size="sm" /><Text role="body" tone="muted">{t('retry.loading')}</Text></Row> : null}
                {cloud.length > 0 && cloudName ? <Text role="caption" tone="muted">{t('retry.onProvider', { provider: cloudName })}</Text> : null}
                {cloud.map((m) => row(m, t('retry.cloudHint')))}
                {local.length > 0 && cloudName ? <Text role="caption" tone="muted">{t('retry.onThisPc', { provider: cloudName })}</Text> : null}
                {local.map((m) => row(m, t('retry.localHint')))}
                {choices.data && cloud.length === 0 && local.length === 0 ? <Text role="body" tone="muted">{t('retry.none')}</Text> : null}
            </Stack>
        </Sheet>
    );
}
