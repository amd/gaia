// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MemoryStore } from '../services/memoryApi';

/** `GET /api/memory/store` with the Python agent selected and no other memory on the PC. */
export const PYTHON_MEMORY_STORE: MemoryStore = {
    agent: 'gaiapy',
    other_available: false,
    supports: {
        due_dates: true, sources: true, sensitive_toggle: true, recategorize: true,
        goals: true, profile: true, advanced: true,
    },
};

/** The same answer with gaiacpp selected: its store keeps the text of a memory and little else. */
export const NATIVE_MEMORY_STORE: MemoryStore = {
    agent: 'gaiacpp',
    other_available: true,
    supports: {
        due_dates: false, sources: false, sensitive_toggle: false, recategorize: false,
        goals: false, profile: false, advanced: false,
    },
};
