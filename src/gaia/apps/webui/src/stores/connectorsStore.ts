// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Issue #915 — store for OAuth connections + per-agent grants.
 *
 * Mirrors the Zustand pattern used elsewhere (notificationStore,
 * permissionStore). The SSE hook (`useConnectorsSSE`) calls the setters
 * here in response to live `connection.connected` / `connection.revoked`
 * / `grant.added` / `grant.removed` events from the FastAPI router.
 */

import { create } from 'zustand';
import * as api from '../services/api';
import type { ConnectorInfo } from '../types';

interface ConnectionsState {
    connections: ConnectorInfo[];
    /** provider → agent_id → scopes[] */
    grants: Record<string, Record<string, string[]>>;
    loading: boolean;
    error: string | null;

    /** Initial load — populates connections + grants in one round-trip. */
    refresh: () => Promise<void>;

    setConnections: (conns: ConnectorInfo[]) => void;
    addConnection: (conn: ConnectorInfo) => void;
    removeConnection: (provider: string) => void;

    setGrants: (provider: string, grants: Record<string, string[]>) => void;
    addGrant: (provider: string, agentId: string, scopes: string[]) => void;
    removeGrant: (provider: string, agentId: string) => void;

    setError: (msg: string | null) => void;
}

export const useConnectionsStore = create<ConnectionsState>((set, get) => ({
    connections: [],
    grants: {},
    loading: false,
    error: null,

    refresh: async () => {
        set({ loading: true, error: null });
        try {
            const { connections } = await api.listConnections();
            // Pull grants for every connected provider.
            const grants: Record<string, Record<string, string[]>> = {};
            await Promise.all(
                connections.map(async (c) => {
                    try {
                        const r = await api.listAgentGrants(c.provider);
                        grants[c.provider] = r.grants;
                    } catch {
                        grants[c.provider] = {};
                    }
                }),
            );
            set({ connections, grants, loading: false });
        } catch (e) {
            set({
                error: e instanceof Error ? e.message : String(e),
                loading: false,
            });
        }
    },

    setConnections: (conns) => set({ connections: conns }),
    addConnection: (conn) =>
        set((s) => {
            const without = s.connections.filter((c) => c.provider !== conn.provider);
            return { connections: [...without, conn] };
        }),
    removeConnection: (provider) =>
        set((s) => ({
            connections: s.connections.filter((c) => c.provider !== provider),
        })),

    setGrants: (provider, grants) =>
        set((s) => ({ grants: { ...s.grants, [provider]: grants } })),
    addGrant: (provider, agentId, scopes) =>
        set((s) => ({
            grants: {
                ...s.grants,
                [provider]: { ...(s.grants[provider] ?? {}), [agentId]: scopes },
            },
        })),
    removeGrant: (provider, agentId) =>
        set((s) => {
            const next = { ...(s.grants[provider] ?? {}) };
            delete next[agentId];
            return { grants: { ...s.grants, [provider]: next } };
        }),

    setError: (msg) => set({ error: msg }),
}));
