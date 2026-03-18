// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for OS agent management state.
 *
 * Uses Record<string, T> (not Map) for Zustand devtools/persist compatibility.
 * See spec: docs/spec/agent-ui-server.mdx
 */

import { create } from 'zustand';
import type {
  AgentInfo,
  AgentStatus,
  AgentInstallProgress,
  AgentConfig,
} from '../types/agent';
import { useAgentChatStore } from './agentChatStore';

// ── State Interface ──────────────────────────────────────────────────────

interface AgentState {
  /** Agent manifest (id → info). Populated from agent-manifest.json. */
  agents: Record<string, AgentInfo>;
  /** Live status per agent (id → status). Updated via IPC polling. */
  statuses: Record<string, AgentStatus>;
  /** Per-agent config (auto-start, restart-on-crash, log level). */
  configs: Record<string, AgentConfig>;
  /** Active install/download progress. Cleared when complete or failed. */
  installProgress: Record<string, AgentInstallProgress>;

  // ── UI State ─────────────────────────────────────────────────────────
  /** Currently selected agent in the Agent Manager panel. */
  selectedAgentId: string | null;
  /** Whether the Agent Manager panel is visible. */
  showAgentManager: boolean;
  /** Whether the agent config dialog is open (for selectedAgentId). */
  showConfigDialog: boolean;
  /** Whether manifest is being fetched. */
  isLoadingManifest: boolean;
  /** Error message from last failed operation. */
  lastError: string | null;

  // ── Agent Data Actions ───────────────────────────────────────────────
  setAgents: (agents: Record<string, AgentInfo>) => void;
  updateAgent: (id: string, updates: Partial<AgentInfo>) => void;
  removeAgent: (id: string) => void;

  // ── Status Actions ───────────────────────────────────────────────────
  setStatus: (id: string, status: AgentStatus) => void;
  setAllStatuses: (statuses: Record<string, AgentStatus>) => void;
  /** Mark agent as no longer running (e.g., after crash or stop). */
  clearRunningState: (id: string) => void;

  // ── Config Actions ───────────────────────────────────────────────────
  setConfig: (id: string, config: AgentConfig) => void;
  setConfigs: (configs: Record<string, AgentConfig>) => void;

  // ── Install Actions ──────────────────────────────────────────────────
  setInstallProgress: (id: string, progress: AgentInstallProgress) => void;
  clearInstallProgress: (id: string) => void;

  // ── UI Actions ───────────────────────────────────────────────────────
  setSelectedAgent: (id: string | null) => void;
  setShowAgentManager: (show: boolean) => void;
  setShowConfigDialog: (show: boolean) => void;
  setLoadingManifest: (loading: boolean) => void;
  setLastError: (error: string | null) => void;

  // ── Lifecycle Actions (delegate to Electron IPC) ─────────────────────
  startAgent: (id: string) => Promise<void>;
  stopAgent: (id: string) => Promise<void>;
  restartAgent: (id: string) => Promise<void>;
  fetchManifest: () => Promise<void>;
  installAgent: (id: string) => Promise<void>;
  uninstallAgent: (id: string) => Promise<void>;
  refreshStatuses: () => Promise<void>;
}

// ── Default agent config ─────────────────────────────────────────────────

/** Default config applied when registering a new agent without explicit config. */
export const DEFAULT_AGENT_CONFIG: AgentConfig = {
  autoStart: false,
  restartOnCrash: true,
  logLevel: 'info',
};

// ── Store Implementation ─────────────────────────────────────────────────

export const useAgentStore = create<AgentState>((set, get) => ({
  // State
  agents: {},
  statuses: {},
  configs: {},
  installProgress: {},
  selectedAgentId: null,
  showAgentManager: false,
  showConfigDialog: false,
  isLoadingManifest: false,
  lastError: null,

  // ── Agent Data Actions ───────────────────────────────────────────────

  setAgents: (agents) => set({ agents }),

  updateAgent: (id, updates) =>
    set((state) => {
      const existing = state.agents[id];
      if (!existing) return state;
      return {
        agents: { ...state.agents, [id]: { ...existing, ...updates } },
      };
    }),

  removeAgent: (id) =>
    set((state) => {
      const { [id]: _removed, ...rest } = state.agents;
      const { [id]: _removedStatus, ...restStatuses } = state.statuses;
      const { [id]: _removedConfig, ...restConfigs } = state.configs;
      return { agents: rest, statuses: restStatuses, configs: restConfigs };
    }),

  // ── Status Actions ───────────────────────────────────────────────────

  setStatus: (id, status) =>
    set((state) => ({
      statuses: { ...state.statuses, [id]: status },
    })),

  setAllStatuses: (statuses) => set({ statuses }),

  clearRunningState: (id) =>
    set((state) => {
      const existing = state.statuses[id];
      if (!existing) return state;
      return {
        statuses: {
          ...state.statuses,
          [id]: {
            ...existing,
            running: false,
            pid: undefined,
            uptime: undefined,
            memoryMB: undefined,
            healthy: undefined,
          },
        },
      };
    }),

  // ── Config Actions ───────────────────────────────────────────────────

  setConfig: (id, config) =>
    set((state) => ({
      configs: { ...state.configs, [id]: config },
    })),

  setConfigs: (configs) => set({ configs }),

  // ── Install Actions ──────────────────────────────────────────────────

  setInstallProgress: (id, progress) =>
    set((state) => ({
      installProgress: { ...state.installProgress, [id]: progress },
    })),

  clearInstallProgress: (id) =>
    set((state) => {
      const { [id]: _removed, ...rest } = state.installProgress;
      return { installProgress: rest };
    }),

  // ── UI Actions ───────────────────────────────────────────────────────

  setSelectedAgent: (id) => set({ selectedAgentId: id }),
  setShowAgentManager: (show) => set({ showAgentManager: show }),
  setShowConfigDialog: (show) => set({ showConfigDialog: show }),
  setLoadingManifest: (loading) => set({ isLoadingManifest: loading }),
  setLastError: (error) => set({ lastError: error }),

  // ── Lifecycle Actions ────────────────────────────────────────────────

  startAgent: async (id) => {
    const api = window.gaiaAPI;
    if (!api) {
      set({ lastError: 'Electron API not available (running in browser?)' });
      return;
    }
    try {
      set({ lastError: null });
      await api.agent.start(id);
      // Status will be updated by the IPC status polling loop
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to start ${id}: ${message}` });
      console.error(`[agentStore] Failed to start agent ${id}:`, err);
    }
  },

  stopAgent: async (id) => {
    const api = window.gaiaAPI;
    if (!api) {
      set({ lastError: 'Electron API not available (running in browser?)' });
      return;
    }
    try {
      set({ lastError: null });
      await api.agent.stop(id);
      get().clearRunningState(id);
      // Clear waiting state in agentChatStore to prevent stuck spinners
      useAgentChatStore.getState().clearWaitingState(id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to stop ${id}: ${message}` });
      console.error(`[agentStore] Failed to stop agent ${id}:`, err);
    }
  },

  restartAgent: async (id) => {
    const api = window.gaiaAPI;
    if (!api) {
      set({ lastError: 'Electron API not available (running in browser?)' });
      return;
    }
    try {
      set({ lastError: null });
      await api.agent.restart(id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to restart ${id}: ${message}` });
      console.error(`[agentStore] Failed to restart agent ${id}:`, err);
    }
  },

  fetchManifest: async () => {
    // In Electron: fetch manifest via IPC
    // In browser: could fetch from a REST endpoint or use mock data
    set({ isLoadingManifest: true, lastError: null });
    try {
      // TODO: Implement IPC call to fetch manifest from main process
      // For now, this is a placeholder — main process will provide
      // agent-manifest.json data via IPC in T2/T7.
      console.log('[agentStore] fetchManifest: not yet implemented (needs IPC)');
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to fetch manifest: ${message}` });
      console.error('[agentStore] Failed to fetch manifest:', err);
    } finally {
      set({ isLoadingManifest: false });
    }
  },

  installAgent: async (id) => {
    const { agents } = get();
    const agent = agents[id];
    if (!agent) {
      set({ lastError: `Unknown agent: ${id}` });
      return;
    }
    // Set initial install progress
    set((state) => ({
      installProgress: {
        ...state.installProgress,
        [id]: { agentId: id, state: 'downloading', progress: 0 },
      },
      lastError: null,
    }));
    try {
      // TODO: Implement IPC call to agent-installer.js in main process (T7)
      console.log(`[agentStore] installAgent(${id}): not yet implemented (needs IPC)`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set((state) => ({
        installProgress: {
          ...state.installProgress,
          [id]: { agentId: id, state: 'failed', progress: 0, error: message },
        },
        lastError: `Failed to install ${agent.name}: ${message}`,
      }));
      console.error(`[agentStore] Failed to install agent ${id}:`, err);
    }
  },

  uninstallAgent: async (id) => {
    const api = window.gaiaAPI;
    if (!api) {
      set({ lastError: 'Electron API not available (running in browser?)' });
      return;
    }
    try {
      set({ lastError: null });
      // Stop the agent if it's running before uninstalling
      const { statuses } = get();
      if (statuses[id]?.running) {
        await get().stopAgent(id);
      }
      // TODO: IPC call to agent-installer.js to remove binary from ~/.gaia/agents/ (T7)
      // Once implemented, place the IPC call HERE and only call removeAgent after it succeeds:
      //   await api.agent.uninstall(id);
      //   get().removeAgent(id);
      console.warn(`[agentStore] uninstallAgent(${id}): IPC not yet implemented — agent state will not be removed until T7 is wired`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to uninstall ${id}: ${message}` });
      console.error(`[agentStore] Failed to uninstall agent ${id}:`, err);
    }
  },

  refreshStatuses: async () => {
    const api = window.gaiaAPI;
    if (!api) return;
    try {
      const statuses = await api.agent.statusAll();
      set({ statuses });
    } catch (err) {
      console.error('[agentStore] Failed to refresh statuses:', err);
    }
  },
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get sorted list of agents (installed first, then by name). */
export const selectSortedAgents = (state: AgentState): AgentInfo[] => {
  const agents = [...Object.values(state.agents)];
  return agents.sort((a, b) => {
    const aInstalled = state.statuses[a.id]?.installed ?? false;
    const bInstalled = state.statuses[b.id]?.installed ?? false;
    if (aInstalled !== bInstalled) return aInstalled ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
};

/** Get count of running agents. */
export const selectRunningCount = (state: AgentState): number =>
  Object.values(state.statuses).filter((s) => s.running).length;

/** Get count of agents with active install progress. */
export const selectInstallingCount = (state: AgentState): number =>
  Object.values(state.installProgress).filter(
    (p) => p.state === 'downloading' || p.state === 'verifying' || p.state === 'installing'
  ).length;
