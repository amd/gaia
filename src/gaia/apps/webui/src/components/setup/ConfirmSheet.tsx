// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../hooks/useFocusTrap';

interface ConfirmSheetProps {
    title: string;
    children?: ReactNode;
    confirmLabel: string;
    cancelLabel: string;
    onConfirm: () => void;
    onCancel: () => void;
    /** Danger styling for an action that deletes something. */
    danger?: boolean;
    busy?: boolean;
}

/**
 * A confirmation that asks before something costly: focus is trapped, starts
 * on the safe choice, and Escape cancels. Rendered over the page, so it reads
 * the same in setup and inside the Settings dialog.
 */
export function ConfirmSheet({ title, children, confirmLabel, cancelLabel, onConfirm, onCancel, danger, busy }: ConfirmSheetProps) {
    const panel = useRef<HTMLDivElement>(null);
    const safe = useRef<HTMLButtonElement>(null);
    const titleId = useId();
    const bodyId = useId();
    useFocusTrap(panel, true);

    useEffect(() => {
        const returnTo = document.activeElement as HTMLElement | null;
        safe.current?.focus();
        return () => returnTo?.focus?.();
    }, []);

    // The top layer takes Escape, so Settings underneath stays open.
    useEscapeToClose(true, onCancel, !busy);

    return createPortal(
        <div className="confirm-overlay">
            <div
                ref={panel}
                className="confirm-sheet"
                role="alertdialog"
                aria-modal="true"
                aria-labelledby={titleId}
                aria-describedby={children ? bodyId : undefined}
            >
                <h2 id={titleId} className="confirm-title">{title}</h2>
                {children && <div id={bodyId} className="confirm-body">{children}</div>}
                <div className="confirm-actions">
                    <button ref={safe} type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
                        {cancelLabel}
                    </button>
                    <button type="button" className={danger ? 'btn-danger' : 'btn-primary'} onClick={onConfirm} disabled={busy}>
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
}
