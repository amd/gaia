// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, type ReactNode } from 'react';
import { AccessibilityInfo, LayoutAnimation } from 'react-native';

export const REVEAL_MS = 180;

/**
 * Children that grow open and collapse shut (180 ms). A closed one is not mounted, so it is gone
 * from the accessibility tree. Reduced motion makes it instant.
 */
export function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
    const [reduceMotion, setReduceMotion] = useState(false);
    const [last, setLast] = useState(open);

    useEffect(() => {
        void AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion);
    }, []);

    // configureNext must run before the commit whose layout change it animates.
    if (last !== open) {
        setLast(open);
        if (!reduceMotion) {
            LayoutAnimation.configureNext({
                duration: REVEAL_MS,
                create: { type: 'easeInEaseOut', property: 'opacity' },
                update: { type: 'easeInEaseOut' },
                delete: { type: 'easeInEaseOut', property: 'opacity' },
            });
        }
    }
    return open ? <>{children}</> : null;
}
