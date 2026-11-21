// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { X } from 'lucide-react';
import './ExportErrorBar.css';

/** Why the last Export (or another chat action) failed, shown above the composer until dismissed. */
export function ExportErrorBar({ message, onDismiss, dismissLabel }: {
    message: string | null;
    onDismiss: () => void;
    dismissLabel: string;
}) {
    if (!message) return null;
    return (
        <div className="export-error-bar" role="alert">
            <span>{message}</span>
            <button type="button" className="btn-icon-sm" onClick={onDismiss} aria-label={dismissLabel}>
                <X size={13} />
            </button>
        </div>
    );
}
