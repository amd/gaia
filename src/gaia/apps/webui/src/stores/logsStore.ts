// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

/** The log groups the viewer has tabs for, in its order. */
export const LOG_SOURCES = ['model_server', 'gaia', 'service', 'agent', 'desktop', 'other'] as const;
export type LogSource = typeof LOG_SOURCES[number];

interface LogsState {
    open: boolean;
    source: LogSource;
    /** Open the viewer on `source`; any surface can. */
    show: (source?: LogSource) => void;
    hide: () => void;
}

export const useLogsStore = create<LogsState>((set) => ({
    open: false,
    source: 'model_server',
    show: (source = 'model_server') => set({ open: true, source }),
    hide: () => set({ open: false }),
}));
