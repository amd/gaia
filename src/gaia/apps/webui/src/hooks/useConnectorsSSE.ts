// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Issue #915 — subscribe to /api/connectors/events and update the store.
 *
 * The router emits five event types:
 *   - connection.connected   {provider, account_email}
 *   - connection.revoked     {provider}
 *   - grant.added            {provider, agent_id, scopes}
 *   - grant.removed          {provider, agent_id}
 *   - flow.*                 (flow lifecycle — currently informational)
 *
 * Connection failures retry with exponential backoff up to 30 seconds.
 */

import { useEffect } from 'react';
import { useConnectionsStore } from '../stores/connectorsStore';
import { getApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';

const logger = log.api;

interface SseEnvelope {
    type: string;
    payload: Record<string, unknown>;
}

export function useConnectorsSSE() {
    const refresh = useConnectionsStore((s) => s.refresh);
    const removeConnection = useConnectionsStore((s) => s.removeConnection);
    const addGrant = useConnectionsStore((s) => s.addGrant);
    const removeGrant = useConnectionsStore((s) => s.removeGrant);

    useEffect(() => {
        const url = `${getApiBase()}/connections/events`;
        let es: EventSource | null = null;
        let backoff = 1000;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let cancelled = false;

        const connect = () => {
            if (cancelled) return;
            es = new EventSource(url);

            es.onmessage = (event) => {
                try {
                    const env = JSON.parse(event.data) as SseEnvelope;
                    const { type, payload } = env;
                    switch (type) {
                        case 'connection.connected':
                            // Easier than reconciling — refetch authoritative state.
                            void refresh();
                            break;
                        case 'connection.revoked':
                            removeConnection(payload.provider as string);
                            break;
                        case 'grant.added':
                            addGrant(
                                payload.provider as string,
                                payload.agent_id as string,
                                (payload.scopes as string[]) ?? [],
                            );
                            break;
                        case 'grant.removed':
                            removeGrant(
                                payload.provider as string,
                                payload.agent_id as string,
                            );
                            break;
                        default:
                            logger.debug('connections-sse: ignoring event', type);
                    }
                } catch (e) {
                    logger.warn('connections-sse: malformed event', e);
                }
            };

            es.onerror = () => {
                es?.close();
                es = null;
                if (cancelled) return;
                timer = setTimeout(connect, backoff);
                backoff = Math.min(backoff * 2, 30_000);
            };
        };

        connect();

        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
            es?.close();
        };
    }, [refresh, removeConnection, addGrant, removeGrant]);
}
