// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// jsdom ships no scrollIntoView, and any suite that renders a transcript calls
// it. Defined here so a new suite inherits it instead of discovering the gap.
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
});

// `waitFor` keeps its own clock, so vitest's testTimeout does not cover it.
// One second is a scheduling accident away on a contended runner.
configure({ asyncUtilTimeout: 10_000 });

// RTL auto-cleanup relies on globals being injected; with globals: false
// we register the cleanup hook explicitly.
afterEach(() => {
    cleanup();
});
