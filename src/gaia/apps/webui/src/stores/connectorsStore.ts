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

    /**
     * User's explicit mail-provider choice for the next email session.
     * Undefined means "no explicit preference" (auto-select if only one is connected).
     */
    pendingMailProvider: string | undefined;

    /** Initial load — populates connections + grants in one round-trip. */
    refresh: () => Promise<void>;

    setConnections: (conns: ConnectorInfo[]) => void;
    addConnection: (conn: ConnectorInfo) => void;
    removeConnection: (provider: string) => void;

    setGrants: (provider: string, grants: Record<string, string[]>) => void;
    addGrant: (provider: string, agentId: string, scopes: string[]) => void;
    removeGrant: (provider: string, agentId: string) => void;

    setError: (msg: string | null) => void;

    /** Set the mail provider the user explicitly chose in the selector. */
    setPendingMailProvider: (provider: string | undefined) => void;
}

export const useConnectionsStore = create<ConnectionsState>((set, get) => ({
    connections: [],
    grants: {},
    loading: false,
    error: null,
    pendingMailProvider: undefined,

    refresh: async () => {
        set({ loading: true, error: null });
        try {
            const { connectors } = await api.listConnectors();
            const connected = connectors.filter(
                (c) => c.type === 'oauth_pkce' && c.configured === true,
            );
            const connections: ConnectorInfo[] = connected.map((c) => ({
                provider: c.id,
                account_email: c.account_id ?? '',
                scopes: c.scopes ?? [],
                connected_at: null,
            }));
            // Pull grants for every connected provider.
            const grants: Record<string, Record<string, string[]>> = {};
            await Promise.all(
                connected.map(async (c) => {
                    try {
                        const r = await api.listConnectorGrants(c.id);
                        grants[c.id] = r.grants;
                    } catch {
                        grants[c.id] = {};
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

    setConnections: (conns) =>
        set((s) => {
            const connectedProviders = new Set(conns.map((c) => c.provider));
            const pendingStillConnected = s.pendingMailProvider === undefined || connectedProviders.has(s.pendingMailProvider);
            return {
                connections: conns,
                ...(pendingStillConnected ? {} : { pendingMailProvider: undefined }),
            };
        }),
    addConnection: (conn) =>
        set((s) => {
            const without = s.connections.filter((c) => c.provider !== conn.provider);
            return { connections: [...without, conn] };
        }),
    removeConnection: (provider) =>
        set((s) => ({
            connections: s.connections.filter((c) => c.provider !== provider),
            // Clear the pending choice if the provider it referenced was disconnected.
            ...(s.pendingMailProvider === provider ? { pendingMailProvider: undefined } : {}),
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

    setPendingMailProvider: (provider) => set({ pendingMailProvider: provider }),
}));
