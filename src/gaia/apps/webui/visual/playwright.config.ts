// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Visual-regression snapshots of the Agent UI's key screens, in both themes.
 *
 * Runs the production build under `vite preview` with every /api call answered
 * from fixtures (visual/fixtures.ts), a frozen clock and animations off, so a
 * diff means the pixels changed and nothing else did.
 *
 *   npm run test:visual                 # compare against the baselines
 *   npm run test:visual -- --update-snapshots
 *
 * A local tool, not a CI gate: baselines are committed for Windows only (the
 * snapshot path includes {platform}), so on Linux or macOS the first run reports
 * missing snapshots. Pass --update-snapshots there to create your own.
 *
 * Baselines carry the OS in their name, since font rasterisation differs per
 * platform. Set GAIA_VISUAL_CHROMIUM to a Chromium or Chrome binary to use one
 * already on the machine instead of `npx playwright install chromium`.
 */

import { defineConfig } from '@playwright/test';

const PORT = 5315;
const executablePath = process.env.GAIA_VISUAL_CHROMIUM || undefined;

export default defineConfig({
    testDir: '.',
    testMatch: '*.visual.ts',
    outputDir: 'test-results',
    snapshotPathTemplate: '{testDir}/__screenshots__/{arg}-{projectName}-{platform}{ext}',
    fullyParallel: true,
    reporter: [['list']],
    expect: {
        toHaveScreenshot: { animations: 'disabled', caret: 'hide', maxDiffPixels: 50 },
    },
    use: {
        baseURL: `http://127.0.0.1:${PORT}`,
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        launchOptions: { executablePath },
    },
    projects: [
        { name: 'light', use: { colorScheme: 'light' } },
        { name: 'dark', use: { colorScheme: 'dark' } },
    ],
    webServer: {
        command: `npx vite preview --port ${PORT} --strictPort --host 127.0.0.1`,
        cwd: '..',
        url: `http://127.0.0.1:${PORT}`,
        reuseExistingServer: false,
        timeout: 60_000,
    },
});
