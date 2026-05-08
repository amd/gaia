// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Per-tile overflow menu for Settings → Connectors (#1004).
 *
 * Shows a small ⋯ button in the tile header. Clicking opens a dropdown
 * with quick actions:
 *
 *   - For configured + enabled MCP connectors:  Disable, Disconnect
 *   - For configured + disabled MCP connectors: Enable,  Disconnect
 *   - For not-configured MCP connectors:        nothing (button hidden)
 *   - For oauth_pkce connectors:                nothing (button hidden)
 *
 * The component is self-positioning (no portal): a relative-positioned
 * wrapper holds the trigger button and the absolutely-positioned popup.
 * Click-outside closes the menu via a document-level listener; Escape
 * also closes.
 */

import { useEffect, useRef, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import * as api from '../services/api';
import type { ConnectorRow } from '../types';

interface Props {
    connector: ConnectorRow;
    /** Called after every successful action so the parent re-fetches. */
    onChanged: () => void;
}

/**
 * The menu is meaningful only for configured MCP connectors. Returning
 * ``null`` from the component is the simplest way to suppress it for
 * everything else.
 */
function shouldRenderMenu(connector: ConnectorRow): boolean {
    return connector.type === 'mcp_server' && connector.configured;
}

export function ConnectorTileMenu({ connector, onChanged }: Props) {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);

    // Close on click outside.
    useEffect(() => {
        if (!open) return;
        const handler = (e: MouseEvent) => {
            if (
                wrapperRef.current &&
                !wrapperRef.current.contains(e.target as Node)
            ) {
                setOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    // Close on Escape.
    useEffect(() => {
        if (!open) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setOpen(false);
        };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [open]);

    if (!shouldRenderMenu(connector)) return null;

    const stop = (e: React.MouseEvent) => {
        // Prevent the tile from toggling open when the menu trigger is clicked.
        e.stopPropagation();
        e.preventDefault();
    };

    const runAction = async (action: () => Promise<unknown>) => {
        setBusy(true);
        setErr(null);
        try {
            await action();
            setOpen(false);
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div
            ref={wrapperRef}
            className="connector-tile-menu"
            onClick={stop}
            onKeyDown={stop as unknown as React.KeyboardEventHandler<HTMLDivElement>}
        >
            <button
                type="button"
                className="connector-tile-menu-trigger"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-label="Connector actions"
                onClick={(e) => {
                    stop(e);
                    setOpen((v) => !v);
                }}
                disabled={busy}
            >
                <MoreHorizontal size={14} />
            </button>

            {open && (
                <div role="menu" className="connector-tile-menu-popup">
                    {connector.enabled ? (
                        <button
                            role="menuitem"
                            type="button"
                            className="connector-tile-menu-item"
                            onClick={() =>
                                void runAction(() =>
                                    api.disableConnector(connector.id),
                                )
                            }
                            disabled={busy}
                        >
                            Disable
                        </button>
                    ) : (
                        <button
                            role="menuitem"
                            type="button"
                            className="connector-tile-menu-item"
                            onClick={() =>
                                void runAction(() =>
                                    api.enableConnector(connector.id),
                                )
                            }
                            disabled={busy}
                        >
                            Enable
                        </button>
                    )}
                    <div className="connector-tile-menu-divider" />
                    <button
                        role="menuitem"
                        type="button"
                        className="connector-tile-menu-item connector-tile-menu-item--danger"
                        onClick={() =>
                            void runAction(() =>
                                api.disconnectConnector(connector.id),
                            )
                        }
                        disabled={busy}
                    >
                        Disconnect
                    </button>
                    {err && (
                        <div className="connector-tile-menu-error">{err}</div>
                    )}
                </div>
            )}
        </div>
    );
}
