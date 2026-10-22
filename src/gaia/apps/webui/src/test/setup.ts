// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// jsdom ships no scrollIntoView, and any suite that renders a transcript calls
// it. Defined here so a new suite inherits it instead of discovering the gap.
// Guarded: suites that opt into the node environment (e.g. `@vitest-environment
// node`) still load this file, and there is no HTMLElement there.
if (typeof HTMLElement !== 'undefined') {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });
}

// jsdom has no 2D canvas and prints "Not implemented" for every getContext.
// A context that accepts every call and draws nothing keeps canvas components
// (the new-chat backdrop) quiet; a suite that inspects drawing installs its own.
if (typeof HTMLCanvasElement !== 'undefined') {
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
        configurable: true,
        value: () => new Proxy({} as Record<PropertyKey, unknown>, {
            // Not `then`: a context must not look like a promise to an `await`.
            get: (store, key) => (key in store ? store[key] : key === 'then' ? undefined : () => undefined),
            set: (store, key, value) => { store[key] = value; return true; },
        }),
    });
}

// `waitFor` keeps its own clock, so vitest's testTimeout does not cover it.
// One second is a scheduling accident away on a contended runner.
configure({ asyncUtilTimeout: 10_000 });

// RTL auto-cleanup relies on globals being injected; with globals: false
// we register the cleanup hook explicitly.
afterEach(() => {
    cleanup();
});
