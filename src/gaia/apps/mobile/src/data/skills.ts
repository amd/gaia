// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery } from '@tanstack/react-query';

import type { SkillList } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

/** The skills GAIA can load, and the skill folders it skipped. */
export function useSkills() {
    const client = useGaiaClient();
    return useQuery<SkillList>({
        queryKey: queryKeys.skills,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listSkills({ signal }),
    });
}
