// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Shortfall } from '@webui/components/setup/selection';
import { createContext, useContext } from 'react';

/** The screens the plan step leads to. An opener is unset until its screen is registered in PLAN_SCREENS. */
export interface PlanOpeners {
    /** Customize: the models screen. */
    openModels?: () => void;
    /** Cloud, Continue: the provider and key screen. */
    openKey?: () => void;
    /** "Change how memory is split": the GPU memory screen. */
    openGpu?: () => void;
    /** Low space, Continue: asks to go on anyway, then calls `proceed(true)`. */
    confirmLowDisk?: (short: Shortfall, proceed: (acknowledgeLowDisk: boolean) => void) => void;
}

export const PlanOpenersContext = createContext<PlanOpeners>({});

export function usePlanOpeners(): PlanOpeners {
    return useContext(PlanOpenersContext);
}
