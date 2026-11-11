// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { X } from 'lucide-react';
import './ExportErrorBar.css';

/** Why the last Export failed, shown under the chat header until dismissed. */
export function ExportErrorBar({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
    if (!message) return null;
    return (
        <div className="export-error-bar" role="alert">
            <span>{message}</span>
            <button type="button" className="btn-icon-sm" onClick={onDismiss} aria-label="Dismiss export error">
                <X size={13} />
            </button>
        </div>
    );
}
