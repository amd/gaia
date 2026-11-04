// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** The bar at the bottom of setup; undefined outside setup, null until it mounts. */
const FooterSlot = createContext<HTMLElement | null | undefined>(undefined);
export const SetupFooterSlot = FooterSlot.Provider;

/**
 * A screen's actions, in the same place on every screen: the bar at the bottom
 * of setup, Back at its start and the step's main action last, at its end.
 * Outside setup (the GPU memory panel in Settings) they stay where they are.
 */
export function SetupFooter({ children }: { children: ReactNode }) {
    const slot = useContext(FooterSlot);
    if (slot === undefined) return <div className="setup-row setup-nav">{children}</div>;
    if (slot === null) return null;
    return createPortal(<div className="setup-footer-row">{children}</div>, slot);
}
