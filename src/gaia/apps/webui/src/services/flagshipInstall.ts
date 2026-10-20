// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Install the GAIA agent from the Agent Hub, the same install `gaia init` runs. */

import * as api from './api';
import { FLAGSHIP_AGENT_ID } from '../utils/newTask';

export const INSTALL_POLL_MS = 1_500;

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Start the install and wait for it to finish. Resolves on `completed`,
 * throws with the backend's reason on `failed` or a refused start. An install
 * already running (409) is waited on rather than reported as a failure.
 */
export async function installFlagshipAgent({
    onProgress,
    isCancelled = () => false,
    pollMs = INSTALL_POLL_MS,
}: {
    onProgress?: (status: api.AgentInstallStatus) => void;
    isCancelled?: () => boolean;
    pollMs?: number;
} = {}): Promise<void> {
    try {
        await api.installAgent(FLAGSHIP_AGENT_ID);
    } catch (err) {
        if ((err as { httpStatus?: unknown })?.httpStatus !== 409) throw err;
    }
    while (!isCancelled()) {
        const status = await api.getAgentInstallStatus(FLAGSHIP_AGENT_ID);
        onProgress?.(status);
        if (status.status === 'completed') return;
        if (status.status === 'failed') {
            throw new Error(status.error || 'The GAIA agent could not be installed.');
        }
        await delay(pollMs);
    }
}
