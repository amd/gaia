// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { Disclosure } from './Disclosure';

export interface SettingsGroupProps {
    title: string;
    hint?: string | null;
    count?: number;
    open: boolean;
    onToggle: () => void;
    headerTestID?: string;
    children?: ReactNode;
}

/** The desktop's `.settings-disclosure`: a bordered group whose header shows a chevron, title, hint and count. */
export function SettingsGroup({ title, hint, count, open, onToggle, headerTestID, children }: SettingsGroupProps) {
    return (
        <Disclosure boxed title={title} hint={hint} count={count} open={open} onToggle={onToggle} headerTestID={headerTestID}>
            {children}
        </Disclosure>
    );
}
