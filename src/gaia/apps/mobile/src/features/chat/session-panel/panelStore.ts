// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

export type PanelSectionId = 'answer' | 'outputs' | 'changes' | 'skills' | 'context' | 'tools';

// The web UI's defaults (stores/sessionPanelStore.ts); remembered for the app session only.
const DEFAULT_OPEN: Record<PanelSectionId, boolean> = {
    answer: true,
    outputs: true,
    changes: true,
    skills: true,
    context: false,
    tools: false,
};

interface PanelState {
    sections: Record<PanelSectionId, boolean>;
    setSection(id: PanelSectionId, open: boolean): void;
    /** Which numbers Context & performance shows under the hood. */
    contextScope: 'turn' | 'chat';
    setContextScope(scope: 'turn' | 'chat'): void;
    /** Dev gallery only: where the Changes section starts, so its confirm, conflict and review states can be shot. */
    changesSeed: { phase: 'confirm' | 'conflict'; message?: string } | { review: true } | null;
    setChangesSeed(seed: PanelState['changesSeed']): void;
    /** The Environment confirmation showing: removing a folder, or turning on full access. */
    /** The folder picker is open inside Environment. */
    envPicking: boolean;
    setEnvPicking(picking: boolean): void;
    envConfirm: { kind: 'remove'; path: string } | { kind: 'full' } | null;
    setEnvConfirm(confirm: PanelState['envConfirm']): void;
}

export const useSessionPanelStore = create<PanelState>((set) => ({
    sections: DEFAULT_OPEN,
    setSection: (id, open) => set((state) => ({ sections: { ...state.sections, [id]: open } })),
    contextScope: 'turn',
    setContextScope: (contextScope) => set({ contextScope }),
    changesSeed: null,
    setChangesSeed: (changesSeed) => set({ changesSeed }),
    envPicking: false,
    setEnvPicking: (envPicking) => set({ envPicking }),
    envConfirm: null,
    setEnvConfirm: (envConfirm) => set({ envConfirm }),
}));
