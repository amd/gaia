// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The `setup` phase of test_installers_headless.yml: the golden path through an
 * installed app. That workflow starts the app and the fake Lemonade itself, and
 * names the app's backend in GAIA_E2E_BASE_URL.
 */

import { downloadToReady, expect, expectTestsPassed, sayer, stepTitle, test } from './harness';

const BASE_URL = process.env.GAIA_E2E_BASE_URL as string;

test('the installed app sets itself up from an empty home', async ({ page, shot }) => {
    const say = sayer('en');
    await page.goto(`${BASE_URL}/`);
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 5 * 60_000 });
    await downloadToReady(page, say, shot);
    await shot('ready');
    await expectTestsPassed(page, BASE_URL, ['server', 'chat', 'vision', 'embedding', 'agent']);
});
