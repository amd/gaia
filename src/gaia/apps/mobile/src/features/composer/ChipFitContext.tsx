// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, type ReactNode } from 'react';
import { View } from 'react-native';

import { a11yVisibility } from '@/ui/theme/a11y';

import type { ChipId } from './chipRoom';

interface ChipFit {
    collapsed: readonly ChipId[];
    report(id: ChipId, width: number): void;
}

/** What the composer's chip row tells its chips: who shows an icon alone, and where to report full widths. */
export const ChipFitContext = createContext<ChipFit>({ collapsed: [], report: () => undefined });

export function useChipCollapsed(id: ChipId): boolean {
    return useContext(ChipFitContext).collapsed.includes(id);
}

/** An off-screen copy of a chip at its full width, so the row can tell what fits. */
export function ChipMeasure({ id, children }: { id: ChipId; children: ReactNode }) {
    const { report } = useContext(ChipFitContext);
    // A chip that unmounts (no meter yet) must stop counting.
    useEffect(() => () => report(id, 0), [id, report]);
    return (
        <View
            {...a11yVisibility({ accessibilityElementsHidden: true, importantForAccessibility: 'no-hide-descendants' })}
            pointerEvents="none"
            style={{ position: 'absolute', width: 4000, height: 0, overflow: 'hidden', flexDirection: 'row', alignItems: 'flex-start' }}
        >
            <View onLayout={(e) => report(id, e.nativeEvent.layout.width)}>{children}</View>
        </View>
    );
}
