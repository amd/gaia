// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Subscribe to ``/api/connectors/events`` and notify the caller when a
 * connector's state changes server-side.
 *
 * The router emits these event types (see
 * ``src/gaia/ui/routers/connectors.py:_connector_events``):
 *
 *   - ``connector.configured``        ({connector_id, account_id})
 *   - ``connector.disconnected``      ({connector_id})
 *   - ``connector.tested``            ({connector_id, ok, detail})
 *   - ``connector.enabled``           ({connector_id})
 *   - ``connector.disabled``          ({connector_id})
 *   - ``connector.oauth.completed``   ({connector_id, account_email})
 *   - ``connector.oauth.error``       ({connector_id, error})
 *   - ``connector.grant.changed``     ({connector_id, agent_id, scopes})
 *   - ``connector.activation.changed`` ({connector_id, agent_id, active})
 *
 * For backwards compatibility, the legacy event names emitted by
 * ``src/gaia/connectors/flow.py`` (``connection.connected`` /
 * ``connection.revoked``) are also recognised and treated as
 * connector-state changes — until flow.py is migrated to the new names,
 * the OAuth-completion path emits the legacy event and we still need to
 * refresh on it.
 *
 * Connection failures retry with exponential backoff up to 30 seconds.
 */

import { useEffect, useRef } from 'react';
import { getApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';

const logger = log.api;

interface SseEnvelope {
    type: string;
    payload: Record<string, unknown>;
}

/** Reasons we'd want a consumer to re-fetch a connector. */
export type ConnectorChangeReason =
    | 'configured'
    | 'disconnected'
    | 'enabled'
    | 'disabled'
    | 'oauth_completed'
    | 'oauth_error'
    | 'tested'
    | 'grant_changed'
    | 'activation_changed';

export interface ConnectorChangeEvent {
    /** Which connector changed, if the payload identified one. */
    connectorId: string | null;
    reason: ConnectorChangeReason;
    /** Raw envelope payload — caller can extract typed fields if needed. */
    payload: Record<string, unknown>;
}

/**
 * Map a raw SSE event type to a normalised ``ConnectorChangeReason``.
 * Returns ``null`` for events the UI doesn't need to react to.
 */
function reasonFor(eventType: string): ConnectorChangeReason | null {
    switch (eventType) {
        case 'connector.configured':
            return 'configured';
        case 'connector.disconnected':
            // Legacy flow.py emits ``connection.revoked`` for the same intent.
            return 'disconnected';
        case 'connection.revoked':
            return 'disconnected';
        case 'connector.oauth.completed':
            return 'oauth_completed';
        // Legacy: flow.py currently emits ``connection.connected`` after a
        // successful OAuth exchange. Treat it as oauth_completed so the
        // tile refreshes without waiting for a window-focus event.
        case 'connection.connected':
            return 'oauth_completed';
        case 'connector.oauth.error':
            return 'oauth_error';
        case 'connector.tested':
            return 'tested';
        case 'connector.enabled':
            return 'enabled';
        case 'connector.disabled':
            return 'disabled';
        case 'connector.grant.changed':
            return 'grant_changed';
        case 'connector.activation.changed':
            return 'activation_changed';
        default:
            return null;
    }
}

/**
 * Subscribe to the connector SSE stream. ``onChange`` is invoked for every
 * event the UI cares about; the caller decides whether to re-fetch one
 * connector or the whole list.
 */
export function useConnectorsSSE(
    onChange: (event: ConnectorChangeEvent) => void,
): void {
    // Stable ref so the EventSource isn't torn down/rebuilt every render
    // when the caller passes an inline arrow function.
    const onChangeRef = useRef(onChange);
    useEffect(() => {
        onChangeRef.current = onChange;
    }, [onChange]);

    useEffect(() => {
        const url = `${getApiBase()}/connectors/events`;
        let es: EventSource | null = null;
        let backoff = 1000;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let cancelled = false;

        const connect = () => {
            if (cancelled) return;
            es = new EventSource(url);

            es.onopen = () => {
                // Reset backoff once the stream is healthy.
                backoff = 1000;
            };

            es.onmessage = (event) => {
                try {
                    const env = JSON.parse(event.data) as SseEnvelope;
                    const reason = reasonFor(env.type);
                    if (reason === null) {
                        logger.debug('connectors-sse: ignoring event', env.type);
                        return;
                    }
                    const payload = env.payload ?? {};
                    const rawId =
                        (payload.connector_id as string | undefined) ??
                        (payload.provider as string | undefined) ??
                        null;
                    onChangeRef.current({
                        connectorId: rawId,
                        reason,
                        payload,
                    });
                } catch (e) {
                    logger.warn('connectors-sse: malformed event', e);
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
    }, []);
}
