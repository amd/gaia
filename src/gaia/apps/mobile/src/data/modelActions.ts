// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import { DOWNLOAD_SPINNER_TIMEOUT_MS, LOAD_SPINNER_TIMEOUT_MS } from '@webui/utils/constants';
import type { SystemStatus } from '@webui/types';

import { STATUS_POLL_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

type Action = 'download' | 'load';

const sameModel = (a: string | null | undefined, b: string | null) => !!a && !!b && a.toLowerCase() === b.toLowerCase();

/**
 * Download, re-download and load the PC's model. While one runs the status is re-read every few
 * seconds and the action stays `pending` until the PC shows it finished (or the web's time limit).
 * A load refused because a chat is answering 409s with `loadBlocked` set; `loadAnyway` forces it.
 */
export function useModelActions(modelName: string | null, status: SystemStatus | undefined) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const [pending, setPending] = useState<Action | null>(null);
    const [error, setError] = useState<unknown>(null);
    const [loadBlocked, setLoadBlocked] = useState<string | null>(null);
    const force = useRef(false);
    const sawProgress = useRef(false);

    useEffect(() => {
        if (!pending) return undefined;
        const check = async () => {
            await queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
            const status = queryClient.getQueryData<SystemStatus>(queryKeys.systemStatus);
            if (!status) return;
            if (pending === 'load') {
                if (sameModel(status.model_loaded, modelName)) setPending(null);
                return;
            }
            const progress = status.download_progress;
            const active = progress?.state === 'starting' || progress?.state === 'downloading';
            if (active) sawProgress.current = true;
            if (progress?.state === 'error') {
                setError(new Error(progress.message ?? ''));
                setPending(null);
            } else if ((sawProgress.current && !active) || (!force.current && status.model_downloaded === true)) {
                setPending(null);
            }
        };
        const poll = setInterval(() => void check(), STATUS_POLL_MS);
        const limit = setTimeout(() => setPending(null), pending === 'load' ? LOAD_SPINNER_TIMEOUT_MS : DOWNLOAD_SPINNER_TIMEOUT_MS);
        return () => {
            clearInterval(poll);
            clearTimeout(limit);
        };
    }, [pending, modelName, queryClient]);

    const run = useCallback(
        async (action: Action, call: () => Promise<unknown>) => {
            setError(null);
            setLoadBlocked(null);
            setPending(action);
            try {
                await call();
                void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
            } catch (e) {
                setPending(null);
                if (action === 'load' && e instanceof GaiaApiError && e.status === 409) setLoadBlocked(e.detail);
                else setError(e);
            }
        },
        [queryClient],
    );

    const download = useCallback(
        (again: boolean) => {
            if (!modelName) return Promise.resolve();
            force.current = again;
            sawProgress.current = false;
            return run('download', () => requireClient(client).downloadModel(modelName, { force: again, loadAfter: !status?.model_loaded }));
        },
        [client, modelName, run, status?.model_loaded],
    );

    const load = useCallback(
        (anyway = false) => (modelName ? run('load', () => requireClient(client).loadModel(modelName, anyway)) : Promise.resolve()),
        [client, modelName, run],
    );

    return {
        pending,
        error,
        loadBlocked,
        download,
        load,
        dismissLoadBlocked: () => setLoadBlocked(null),
    };
}
