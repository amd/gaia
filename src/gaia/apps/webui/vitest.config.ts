// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
    plugins: [react()],
    test: {
        environment: 'jsdom',
        globals: false,
        setupFiles: ['./src/test/setup.ts'],
        include: ['src/**/*.{test,spec}.{ts,tsx}'],
        css: true,
        // The transcript/polling suites render a full ChatView and step fake
        // timers; on a contended runner that overruns the 5s default and the
        // suite goes red on scheduling, not on behaviour.
        testTimeout: 30_000,
        hookTimeout: 30_000,
    },
    define: {
        __APP_VERSION__: JSON.stringify('0.0.0-test'),
    },
});
