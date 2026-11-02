// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * First-run setup, walked for real: this checkout's backend and `gaia init`
 * against a fake Lemonade, driven through the served UI in Chromium.
 *
 *   npx playwright test -c e2e/playwright.config.ts
 *
 * Nothing here is mocked in the browser, unlike visual/. Each test starts its
 * own backend and fake on free ports with an empty home (e2e/harness.ts), so
 * tests can run side by side. Every test runs in English and Arabic.
 *
 * Environment:
 *   GAIA_E2E_PYTHON      Python with GAIA installed (default: python)
 *   GAIA_E2E_PYTHONPATH  checkout's src and hub agent, when GAIA is installed elsewhere
 *   GAIA_E2E_UI_DIST     built UI to serve (default: this checkout's dist)
 *   GAIA_E2E_BASE_URL    run only installed.journey.ts, against an app already running
 *   GAIA_VISUAL_CHROMIUM a Chromium or Chrome binary, as in visual/
 */

import { defineConfig } from '@playwright/test';

import type { JourneyOptions } from './harness';

const installed = !!process.env.GAIA_E2E_BASE_URL;
const executablePath = process.env.GAIA_VISUAL_CHROMIUM || undefined;

export default defineConfig<JourneyOptions>({
    testDir: '.',
    testMatch: installed ? 'installed.journey.ts' : 'setup.journey.ts',
    outputDir: 'test-results/output',
    timeout: 8 * 60_000,
    expect: { timeout: 30_000 },
    // A retry would hide exactly what this suite exists to find.
    retries: 0,
    forbidOnly: !!process.env.CI,
    fullyParallel: true,
    workers: process.env.CI ? 2 : 3,
    reporter: [['list'], ['html', { outputFolder: 'test-results/report', open: 'never' }]],
    use: {
        viewport: { width: 1280, height: 860 },
        deviceScaleFactor: 1,
        launchOptions: { executablePath },
        // Without these a missing element waits out the whole test.
        actionTimeout: 60_000,
        navigationTimeout: 60_000,
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
    },
    projects: [
        { name: 'en', use: { lang: 'en' } },
        { name: 'ar', use: { lang: 'ar' } },
    ],
});
