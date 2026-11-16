// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, type ReactNode } from 'react';

import type { GaiaClient } from '@/api/client.types';

const GaiaClientContext = createContext<GaiaClient | null>(null);

export function GaiaClientProvider({ client, children }: { client: GaiaClient | null; children: ReactNode }) {
    return <GaiaClientContext.Provider value={client}>{children}</GaiaClientContext.Provider>;
}

/** The client for the paired PC, or null while no PC is configured. */
export function useGaiaClient(): GaiaClient | null {
    return useContext(GaiaClientContext);
}
