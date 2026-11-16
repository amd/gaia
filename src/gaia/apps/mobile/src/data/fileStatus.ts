// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery } from '@tanstack/react-query';

import type { FileStatusList } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

/** How often listed files are re-checked while their section is shown, so a later delete or edit appears. */
export const FILE_RECHECK_MS = 4000;

/** Whether each path still exists on the PC and when it last changed. */
export function useFileStatus(paths: readonly string[], { enabled }: { enabled: boolean }) {
    const client = useGaiaClient();
    return useQuery<FileStatusList['files']>({
        queryKey: ['fileStatus', ...paths],
        enabled: enabled && client !== null && paths.length > 0,
        queryFn: async ({ signal }) => (await requireClient(client).getFileStatus([...paths], { signal })).files,
        refetchInterval: FILE_RECHECK_MS,
        retry: false,
    });
}
