// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface Props {
    title: string;
    /** Shown as a pill after the title. */
    count?: number;
    /** One line under the title, readable while the section is closed. */
    hint?: string;
    defaultOpen?: boolean;
    /** Holds the section open, e.g. while a search or a download needs its rows seen. */
    forceOpen?: boolean;
    children: ReactNode;
}

/** A collapsible section; its contents stay mounted, so their state survives closing it. */
export function SettingsDisclosure({ title, count, hint, defaultOpen = false, forceOpen = false, children }: Props) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <details
            className="settings-disclosure"
            open={open || forceOpen}
            onToggle={(e) => { if (!forceOpen) setOpen(e.currentTarget.open); }}
        >
            <summary onClick={(e) => { if (forceOpen) e.preventDefault(); }}>
                <ChevronDown size={15} className="settings-disclosure-chevron" aria-hidden="true" />
                <span className="settings-disclosure-text">
                    <span className="settings-disclosure-title">{title}</span>
                    {hint && <span className="settings-disclosure-hint">{hint}</span>}
                </span>
                {count != null && <bdi dir="ltr" className="settings-disclosure-count">{count}</bdi>}
            </summary>
            <div className="settings-disclosure-body">{children}</div>
        </details>
    );
}
