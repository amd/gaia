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
 * Positioning: the popup uses `position: fixed` with viewport coordinates
 * computed from the trigger button's `getBoundingClientRect()`. This lets
 * it escape the `overflow: hidden` on `.connector-tile` without a portal.
 * A scroll + resize listener repositions the popup while it is open so it
 * stays anchored to the trigger as the settings panel scrolls.
 *
 * Click-outside (mousedown on document) and Escape close the menu.
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
    const [popupPos, setPopupPos] = useState<{ top: number; right: number } | null>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const popupRef = useRef<HTMLDivElement>(null);

    // Close on click outside.
    useEffect(() => {
        if (!open) return;
        const handler = (e: MouseEvent) => {
            const target = e.target as Node;
            if (
                triggerRef.current &&
                !triggerRef.current.contains(target) &&
                popupRef.current &&
                !popupRef.current.contains(target)
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

    // Reposition popup on scroll/resize so it stays anchored to the trigger
    // even as the settings panel scrolls. Using capture:true catches scroll
    // on any ancestor (the settings-page-body scroller included).
    useEffect(() => {
        if (!open) return;
        const reposition = () => {
            if (!triggerRef.current) return;
            const rect = triggerRef.current.getBoundingClientRect();
            setPopupPos({
                top: rect.bottom + 4,
                right: window.innerWidth - rect.right,
            });
        };
        window.addEventListener('resize', reposition);
        window.addEventListener('scroll', reposition, true);
        return () => {
            window.removeEventListener('resize', reposition);
            window.removeEventListener('scroll', reposition, true);
        };
    }, [open]);

    if (!shouldRenderMenu(connector)) return null;

    const stop = (e: React.MouseEvent) => {
        // Prevent the tile from toggling open when the menu trigger is clicked.
        e.stopPropagation();
        e.preventDefault();
    };

    const handleTriggerClick = (e: React.MouseEvent) => {
        stop(e);
        if (!open && triggerRef.current) {
            const rect = triggerRef.current.getBoundingClientRect();
            // Anchor popup below-right of trigger; right-align to viewport right edge.
            setPopupPos({
                top: rect.bottom + 4,
                right: window.innerWidth - rect.right,
            });
        }
        setOpen((v) => !v);
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
            className="connector-tile-menu"
            onClick={stop}
            onKeyDown={stop as unknown as React.KeyboardEventHandler<HTMLDivElement>}
        >
            <button
                ref={triggerRef}
                type="button"
                className="connector-tile-menu-trigger"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-label="Connector actions"
                onClick={handleTriggerClick}
                disabled={busy}
            >
                <MoreHorizontal size={14} />
            </button>

            {open && popupPos && (
                <div
                    ref={popupRef}
                    role="menu"
                    className="connector-tile-menu-popup"
                    style={{ position: 'fixed', top: popupPos.top, right: popupPos.right }}
                >
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
