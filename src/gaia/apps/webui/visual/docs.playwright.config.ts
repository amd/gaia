// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Renders the user guide's screenshots (docs/images/agent-ui) from fixtures.
 *
 *   npm run docs:shots
 *
 * Kept apart from playwright.config.ts so `npm run test:visual` never rewrites
 * the docs images. English and the light theme only: the docs site is English.
 */

import { defineConfig } from '@playwright/test';

const PORT = 5316;

export default defineConfig({
    testDir: '.',
    testMatch: 'docs.shots.ts',
    outputDir: 'test-results',
    reporter: [['list']],
    use: {
        baseURL: `http://127.0.0.1:${PORT}`,
        viewport: { width: 1280, height: 800 },
        deviceScaleFactor: 1,
        colorScheme: 'light',
        launchOptions: { executablePath: process.env.GAIA_VISUAL_CHROMIUM || undefined },
    },
    webServer: {
        command: `npx vite preview --port ${PORT} --strictPort --host 127.0.0.1`,
        cwd: '..',
        url: `http://127.0.0.1:${PORT}`,
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
