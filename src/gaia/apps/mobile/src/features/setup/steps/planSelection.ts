// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Selection } from '@webui/components/setup/selection';
import { create } from 'zustand';

interface PlanSelectionState {
    /** The models ticked for this setup; null until the plan has been read and seeded it. */
    selection: Selection | null;
    setSelection(selection: Selection | null): void;
    /** "Keep trying if the connection drops": the run waits up to two minutes out a dropped download. */
    keepTrying: boolean;
    setKeepTrying(keepTrying: boolean): void;
}

/** One selection for the plan step and the Customize screens, so a change there shows in the plan. */
export const usePlanSelection = create<PlanSelectionState>((set) => ({
    selection: null,
    setSelection: (selection) => set({ selection }),
    keepTrying: false,
    setKeepTrying: (keepTrying) => set({ keepTrying }),
}));
