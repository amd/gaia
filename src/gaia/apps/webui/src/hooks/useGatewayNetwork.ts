// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import type { ProviderNetwork } from '../types';

/** Whether the AMD gateway's host answers; null until the backend has said. */
export function useGatewayNetwork(): { network: ProviderNetwork | null; recheck: () => void } {
    const [network, setNetwork] = useState<ProviderNetwork | null>(null);
    const recheck = useCallback(() => {
        Promise.resolve(api.listProviders())
            .then((r) => setNetwork(r?.providers?.find((p) => p.id === 'amd')?.network ?? null))
            .catch((err) => {
                console.warn('[useGatewayNetwork] could not check the AMD gateway', err);
                setNetwork(null);
            });
    }, []);
    useEffect(recheck, [recheck]);
    return { network, recheck };
}
