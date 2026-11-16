// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError } from '@/api/errors';
import type { Translate } from '@/i18n/useT';
import { formatSize } from '@webui/components/setup/format';
import { isolate } from '@webui/i18n/registry';
import type { SetupConfirm } from '@webui/types';

/** What a confirm sheet asks: the caller learns the answer from `useConfirmSheet().ask`. */
export interface ConfirmRequest {
    title: string;
    body: string[];
    confirmLabel: string;
    cancelLabel: string;
    /** Danger styling for a choice that deletes something. */
    danger?: boolean;
}

/** The confirmation a setup choice's 409 asks for, or null for any other error; the web UI's `setupConfirmFrom`. */
export function setupConfirmFrom(err: unknown): SetupConfirm | null {
    if (!(err instanceof GaiaApiError) || err.status !== 409 || !err.body) return null;
    const { code, acknowledge } = err.body;
    if ((code === 'no_fit' || code === 'low_disk') && typeof acknowledge === 'string') {
        return err.body as unknown as SetupConfirm;
    }
    return null;
}

const ltr = (text: string) => isolate(text, 'ltr');

/** The chosen models need more disk than is free: "Not enough free disk space". */
export function lowDiskRequest(short: { needGb: number; freeGb: number; freeUpGb: number }, t: Translate): ConfirmRequest {
    return {
        title: t('setup.confirm.lowDiskTitle'),
        body: [
            t('setup.summary.short', {
                need: ltr(formatSize(short.needGb)),
                free: ltr(formatSize(short.freeGb)),
                drive: t('setup.summary.theDrive'),
                freeUp: ltr(formatSize(short.freeUpGb)),
            }),
        ],
        confirmLabel: t('setup.confirm.downloadAnyway'),
        cancelLabel: t('setup.confirm.changeModels'),
    };
}

/** Chosen models may not fit this PC's memory; `why` is each one's reason, as the model list words it. */
export function noFitRequest(models: { name: string; why: string }[], t: Translate): ConfirmRequest {
    return {
        title: models.length === 1 ? t('setup.confirm.noFitOne', { name: models[0].name }) : t('setup.confirm.noFitMany'),
        body: models.map((m) => `${m.name}: ${m.why}`),
        confirmLabel: t('setup.confirm.downloadAnyway'),
        cancelLabel: t('setup.confirm.pickSmaller'),
    };
}

/** A 409 from the server, worded by `detail.code` and carrying the server's own message. */
export function serverConfirmRequest(detail: SetupConfirm, t: Translate): ConfirmRequest {
    const lowDisk = detail.code === 'low_disk';
    return {
        title: lowDisk ? t('setup.confirm.lowDiskTitle') : t('setup.confirm.noFitServerTitle'),
        body: [detail.message],
        confirmLabel: lowDisk ? t('setup.confirm.downloadAnyway') : t('setup.confirm.useAnyway'),
        cancelLabel: t('setup.confirm.changeModels'),
    };
}
