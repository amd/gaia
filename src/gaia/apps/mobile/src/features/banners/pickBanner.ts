// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaNetworkError } from '@/api/errors';
import type { SystemStatus } from '@/api/client.types';
import { isStarting } from '@/data';
import { describeErrorWith, type Translate } from '@/i18n/useT';
import type { NoticeTone } from '@/ui';
import { GB_UNIT, MIN_CONTEXT_SIZE } from '@webui/utils/constants';

export interface Banner {
    id: 'download' | 'goalStore' | 'starting' | 'unreachable' | 'backendError' | 'configError' | 'serverHung' | 'serverStopped' | 'contextTooSmall';
    tone: NoticeTone;
    title: string;
    message?: string;
    /** The web lets these go: a failed download, a stopped or hung model server, a too-small context window. */
    dismissible?: boolean;
    /** Re-asks GAIA; shown as the "Check again" action. */
    retry?: boolean;
    /** 0 to 1 for a determinate line; undefined for an indeterminate one. */
    progress?: { value?: number; label?: string };
}

const gb = (bytes: number, digits: number) => (bytes / 1e9).toFixed(digits);

/**
 * The one banner the app shows, in the web's ConnectionBanner order (NTF-08..13):
 * download, start-up, backend trouble, model server, context window.
 * `fetchError` is the status request's failure, if it failed.
 */
export function pickBanner(status: SystemStatus | undefined, fetchError: unknown, t: Translate, goalStoreError?: string | null): Banner | null {
    const progress = status?.download_progress ?? null;
    if (progress?.state === 'downloading' || progress?.state === 'starting' || progress?.state === 'error') {
        const name = progress.model_name || status?.default_model_name || '';
        if (progress.state === 'error') {
            return { id: 'download', tone: 'danger', title: t('banner.download.failed', { name }), message: progress.message ?? undefined, dismissible: true };
        }
        const size = progress.total_bytes ? `${gb(progress.total_bytes, 1)} ${GB_UNIT}` : t('banner.download.sizeUnknown');
        const file = progress.file ? `${t('banner.download.file', { index: progress.file_index, total: progress.total_files })} ${progress.file}` : undefined;
        const bar =
            progress.state === 'downloading' && progress.total_bytes > 0
                ? { value: progress.percent / 100, label: `${progress.percent}% · ${gb(progress.downloaded_bytes, 2)} / ${gb(progress.total_bytes, 2)} ${GB_UNIT}` }
                : { label: progress.state === 'starting' ? (progress.message ?? t('update.picker.starting')) : t('banner.download.progress') };
        return { id: 'download', tone: 'info', title: t('banner.download.running', { name, size }), message: file, progress: bar };
    }

    if (fetchError) {
        if (fetchError instanceof GaiaApiError && fetchError.status >= 500) {
            return {
                id: 'backendError',
                tone: 'danger',
                title: t.compose((say) => say('banner.backendError', { error: describeErrorWith(fetchError, say) })),
                message: t('banner.backendErrorHint'),
                retry: true,
            };
        }
        // The PC itself not answering reads as the setup gate does; the assistant wording is for a PC that answers.
        if (fetchError instanceof GaiaNetworkError) {
            return { id: 'unreachable', tone: 'danger', title: t('mobileApp.pairing.off.title'), message: t('mobileApp.pairing.unreachable.body'), retry: true };
        }
        return { id: 'unreachable', tone: 'danger', title: t('banner.unreachable'), message: t('banner.unreachableHint'), retry: true };
    }
    if (!status) return null;

    if (isStarting(status)) {
        const running = status.init_tasks?.find((task) => task.status === 'running');
        return { id: 'starting', tone: 'info', title: running?.name || t('banner.preparing'), progress: {} };
    }

    if (status.config_error) {
        return { id: 'configError', tone: 'warning', title: t('banner.configError'), message: status.config_error };
    }

    if (!status.lemonade_running) {
        // Listening but failing its health check is usually busy, not stopped.
        const hung = Boolean(status.lemonade_error);
        return {
            id: hung ? 'serverHung' : 'serverStopped',
            tone: 'warning',
            title: t(hung ? 'banner.serverHung' : 'banner.serverStopped'),
            message: t('mobileApp.banner.openOnPc'),
            retry: true,
            dismissible: true,
        };
    }

    const size = status.model_context_size;
    if (!status.context_size_sufficient && size) {
        return {
            id: 'contextTooSmall',
            tone: 'warning',
            title: t('banner.contextTooSmall', { current: size.toLocaleString(), required: MIN_CONTEXT_SIZE.toLocaleString() }),
            message: t('banner.contextTooSmallHint'),
            retry: true,
            dismissible: true,
        };
    }
    if (goalStoreError) {
        return { id: 'goalStore', tone: 'warning', title: t('mem.goals.storeError'), message: goalStoreError, dismissible: true };
    }
    return null;
}
