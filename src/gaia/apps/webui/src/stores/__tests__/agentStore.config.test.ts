// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Agent config persistence — issue #4300.
 *
 * Saved agent settings must reach the Electron settings file, and a fresh
 * store (an app restart) must load them back.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useAgentStore } from '../agentStore';
import type { AgentConfig, TrayConfig, TrayConfigUpdate } from '../../types/agent';

const CFG: AgentConfig = { autoStart: true, restartOnCrash: false, logLevel: 'debug' };

/** Minimal stand-in for the main-process handler: merges `agents` by id. */
function installBridge(disk: { agents: Record<string, AgentConfig> }) {
    const tray = {
        getConfig: vi.fn(async (): Promise<TrayConfig> => structuredClone(disk) as TrayConfig),
        setConfig: vi.fn(async (update: TrayConfigUpdate): Promise<TrayConfig> => {
            disk.agents = { ...disk.agents, ...update.agents };
            return structuredClone(disk) as TrayConfig;
        }),
    };
    (window as unknown as { gaiaAPI: unknown }).gaiaAPI = { tray };
    return tray;
}

function resetStore() {
    useAgentStore.setState({ configs: {}, lastError: null });
}

describe('agentStore config persistence (#4300)', () => {
    beforeEach(resetStore);
    afterEach(() => {
        delete (window as unknown as { gaiaAPI?: unknown }).gaiaAPI;
    });

    it('persists a saved config and loads it back after a restart', async () => {
        const disk = { agents: {} };
        const tray = installBridge(disk);

        expect(await useAgentStore.getState().saveConfig('email', CFG)).toBe(true);
        expect(tray.setConfig).toHaveBeenCalledWith({ agents: { email: CFG } });

        resetStore();
        await useAgentStore.getState().loadConfigs();
        expect(useAgentStore.getState().configs).toEqual({ email: CFG });
    });

    it('does not report success or change state when the save fails', async () => {
        const tray = installBridge({ agents: {} });
        tray.setConfig.mockRejectedValueOnce(new Error('disk full'));

        expect(await useAgentStore.getState().saveConfig('email', CFG)).toBe(false);
        expect(useAgentStore.getState().configs).toEqual({});
        expect(useAgentStore.getState().lastError).toMatch(/disk full/);
    });

    it('surfaces a load failure instead of showing defaults silently', async () => {
        const tray = installBridge({ agents: {} });
        tray.getConfig.mockRejectedValueOnce(new Error('bad file'));

        await useAgentStore.getState().loadConfigs();
        expect(useAgentStore.getState().lastError).toMatch(/bad file/);
    });
});
