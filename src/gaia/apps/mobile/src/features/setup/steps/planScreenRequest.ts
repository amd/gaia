// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { PlanScreenName } from './planScreens';

/** A plan screen asked for from another step: the plan step opens it on arrival, and closing it goes back. */
export interface PlanScreenRequest {
    screen: PlanScreenName;
    from: 'check';
}

interface State {
    pending: PlanScreenRequest | null;
    request(request: PlanScreenRequest): void;
    clear(): void;
}

export const usePlanScreenRequest = create<State>((set) => ({
    pending: null,
    request: (request) => set({ pending: request }),
    clear: () => set({ pending: null }),
}));
