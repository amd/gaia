// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * First-run setup's screens, from an empty home. This layer is Chromium against
 * the UI the real backend serves: the Electron shell, its tray and installers
 * are test_installers_headless.yml's. See playwright.config.ts for how to run it.
 */

import {
    CHAT_MODEL, EMBEDDER, UNTESTED_CHAT_MODEL, downloadToReady, expect, expectTestsPassed, stepTitle, test,
    testToReady, type Say,
} from './harness';
import type { Page } from '@playwright/test';

const ALL_MODELS = [CHAT_MODEL, EMBEDDER];
const start = (page: Page, say: Say) => page.getByRole('button', { name: say('setup.plan.start') });
const newChat = (page: Page, say: Say) => page.getByRole('main', { name: say('newChat.label') });

/** The app, set up already: both models are on the fake from the start. */
async function openSetUp(journey: import('./harness').Journey, open: () => Promise<void>, page: Page, say: Say): Promise<void> {
    await journey.startFake({ models: ALL_MODELS });
    await journey.startBackend();
    await open();
    await expect(newChat(page, say)).toBeVisible({ timeout: 2 * 60_000 });
}

test('checks finish with no click, then download reaches Test and Ready, and a second launch skips setup', async ({ page, journey, say, shot, open }) => {
    await journey.startFake({ env: { FAKE_LEMONADE_PULL_SECONDS: '3' } });
    await journey.startBackend();
    // A held plan keeps the checks on screen long enough to read and photograph.
    let held = false;
    await page.route('**/api/setup/plan', async (route) => {
        if (!held) {
            held = true;
            await new Promise((done) => setTimeout(done, 2_000));
        }
        await route.continue();
    });
    await open();
    await expect(stepTitle(page)).toHaveText(say('setup.check.heading'), { timeout: 2 * 60_000 });
    await shot('checks');

    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'));
    await shot('plan');
    expect(journey.pulls(), 'nothing downloads before the user says so').toEqual([]);

    await downloadToReady(page, say, shot);
    await shot('ready');
    await expectTestsPassed(page, journey.url, ['server', 'chat', 'vision', 'embedding', 'agent']);
    expect(journey.pulls().map((r) => r.model).sort()).toEqual([...ALL_MODELS].sort());

    await page.getByRole('button', { name: say('setup.ready.start') }).click();
    await expect(newChat(page, say)).toBeVisible();

    await journey.stopBackend();
    await journey.startBackend();
    const pulled = journey.pulls().length;
    await open();
    await expect(newChat(page, say)).toBeVisible({ timeout: 2 * 60_000 });
    await expect(page.locator('main.setup')).toHaveCount(0);
    await shot('second-launch');
    expect(journey.pulls()).toHaveLength(pulled);
});

test('Customize picks another chat model, vision follows it, and the labels say what is verified', async ({ page, journey, say, shot, open }) => {
    await journey.startFake();
    await journey.startBackend();
    await open();
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 2 * 60_000 });
    await page.getByRole('button', { name: say('setup.plan.customize') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.pc.title'));
    await shot('this-pc');
    await page.getByRole('button', { name: say('setup.continue') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.models.title'));

    const chat = page.locator('.mc-job').first();
    const row = (id: string) => chat.locator('li.mc-row', { has: page.locator(`[title="${id}"]`) });
    await expect(row(CHAT_MODEL).locator('.mc-chip.is-verified')).toHaveText(say('setup.label.verified'));
    await expect(row(UNTESTED_CHAT_MODEL).locator('.mc-chip.is-untested')).toHaveText(say('setup.label.untested'));
    await expect(row(CHAT_MODEL).getByRole('radio')).toBeChecked();
    await expect(page.getByText(say('setup.job.nothingMore'))).toBeVisible();
    await shot('models');

    await row(UNTESTED_CHAT_MODEL).locator('label').click();
    await expect(row(UNTESTED_CHAT_MODEL).getByRole('radio')).toBeChecked();
    await expect(page.getByText(say('setup.job.untestedPicked'))).toBeVisible();
    await expect(page.getByText(say('setup.job.nothingMore'))).toHaveCount(0);
    const vision = page.locator('.mc-job').nth(1);
    await expect(vision.locator('li.mc-row', { has: page.locator(`[title="${CHAT_MODEL}"]`) }).getByRole('radio')).toBeChecked();
    await shot('models-changed');

    await page.getByRole('button', { name: say('setup.continue') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.download.title'));
    await testToReady(page, say, shot);

    await expectTestsPassed(page, journey.url, ['chat', 'vision', 'embedding', 'agent']);
    expect(journey.config().default_model).toBe(UNTESTED_CHAT_MODEL);
    const pulled = journey.pulls();
    expect(pulled.map((r) => r.model).sort()).toEqual([CHAT_MODEL, EMBEDDER, UNTESTED_CHAT_MODEL].sort());
    expect(pulled.find((r) => r.model === UNTESTED_CHAT_MODEL)?.body_keys).toEqual(expect.arrayContaining(['checkpoint', 'recipe']));
});

test('the cloud path greys the gateway with its reason, and offers it once the host answers', async ({ page, journey, say, shot, open }) => {
    const gatewayPort = await journey.closedPort();
    await journey.startFake();
    await journey.startBackend({ GAIA_GATEWAY_BASE_URL: `http://127.0.0.1:${gatewayPort}/Unified/v1` });
    await open();
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 2 * 60_000 });
    await page.getByRole('button', { name: say('setup.plan.cloud') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.key.title'));

    const gateway = page.getByRole('radio', { name: /AMD LLM Gateway/ });
    await expect(gateway).toBeDisabled();
    await expect(gateway).toContainText(say('setup.key.gatewayOff'));
    await shot('gateway-unreachable');

    await journey.listen(gatewayPort);
    await page.getByRole('button', { name: say('setup.key.checkAgain') }).click();
    await expect(gateway).toBeEnabled();
    await expect(gateway).toContainText(say('setup.where.amdNote'));
    await gateway.click();
    await expect(gateway).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('button', { name: say('setup.key.connect') })).toBeEnabled();
    await shot('gateway-reachable');
});

for (const [name, kind] of [['a failed download', 'lemonade'], ['a full disk', 'disk_full']] as const) {
    test(`${name} recovers with Retry and finishes`, async ({ page, journey, say, shot, open }) => {
        await journey.startFake({ env: { FAKE_LEMONADE_PULL_FAIL: kind } });
        await journey.startBackend();
        await open();
        await expect(start(page, say)).toBeVisible({ timeout: 2 * 60_000 });
        await start(page, say).click();

        const failed = page.locator('.dl-row.is-failed');
        await expect(failed).toHaveCount(1, { timeout: 2 * 60_000 });
        const retry = failed.getByRole('button', { name: say('setup.progress.retry') });
        await expect(retry).toBeVisible({ timeout: 2 * 60_000 });
        await expect(failed.locator('.dl-error')).toBeVisible();
        await shot('failed');
        const status = await (await page.request.get(`${journey.url}/api/setup/status`)).json();
        expect(status.models.find((m: { state: string }) => m.state === 'failed').error_kind).toBe(kind);

        await retry.click();
        await page.getByRole('button', { name: say('setup.finish') }).click({ timeout: 60_000 });
        await testToReady(page, say, shot);
        await expectTestsPassed(page, journey.url, ['chat', 'embedding', 'agent']);
        await shot('recovered');
    });
}

test('a model server that will not start recovers with Try again', async ({ page, journey, say, shot, open }) => {
    const port = await journey.startBackendWithoutServer();
    await open();
    const alert = page.locator('.setup-error[role="alert"]');
    await expect(alert).toBeVisible({ timeout: 3 * 60_000 });
    await expect(stepTitle(page)).toHaveText(say('setup.check.heading'));
    await shot('server-down');

    await journey.startFake({ port });
    // Hard-coded English in the server-problem card today, so either wording counts.
    await page.getByRole('button', { name: 'Try again' }).or(page.getByRole('button', { name: say('setup.tryAgain') })).first().click();
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 2 * 60_000 });
    await shot('server-back');
});

test('finishing setup without choosing a GPU size changes nothing', async ({ page, journey, say, shot, open }) => {
    await journey.startFake({ env: { FAKE_LEMONADE_GPU: 'igpu' } });
    await journey.startBackend();
    const gpuWrites: string[] = [];
    page.on('request', (r) => {
        if (r.method() !== 'GET' && r.url().includes('/api/gpu-memory')) gpuWrites.push(`${r.method()} ${r.url()}`);
    });
    await open();
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 2 * 60_000 });
    await page.getByRole('button', { name: say('setup.plan.customize') }).click();
    await page.getByRole('button', { name: say('setup.continue') }).click();
    // GAIA offers this step on Windows and Linux only.
    if (process.platform !== 'darwin') {
        await expect(stepTitle(page)).toHaveText(say('setup.gpu.title'));
        await shot('gpu-step');
        await page.getByRole('button', { name: say('gpu.skip') }).click();
    }
    await expect(stepTitle(page)).toHaveText(say('setup.models.title'));
    await page.getByRole('button', { name: say('setup.continue') }).click();
    await testToReady(page, say, shot);

    expect(gpuWrites, 'GPU memory requests that change something').toEqual([]);
    expect(journey.hasGaiaFile('gpu_memory.json'), 'a recorded GPU change').toBe(false);
    await expect(page.locator('.setup-summary-list').getByText(say('setup.ready.gpu'))).toHaveCount(0);
    await shot('ready');
});

test('Settings runs setup again', async ({ page, journey, say, shot, open }) => {
    await openSetUp(journey, open, page, say);
    await page.getByRole('button', { name: say('sidebar.settings') }).first().click();
    await page.getByRole('dialog', { name: say('sidebar.settings') }).getByRole('button', { name: say('settings.section.setup'), exact: true }).click();
    await page.getByTestId('setup-run-again').click();
    await page.getByRole('button', { name: say('settings.setup.confirmYes') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.plan.title'), { timeout: 2 * 60_000 });
    await shot('run-again');
    await page.getByRole('button', { name: say('setup.plan.startNoDownload') }).click();
    await testToReady(page, say, shot);
    expect(journey.pulls(), 'everything was already on this PC').toEqual([]);
});

test('with no models, a chat asks to download one', async ({ page, journey, lang, say, shot, open }) => {
    await openSetUp(journey, open, page, say);
    await journey.deleteModel(CHAT_MODEL);
    await journey.deleteModel(EMBEDDER);
    await newChat(page, say).getByRole('textbox').fill('Hello');
    await page.keyboard.press('Enter');
    const dialog = lang === 'en' ? page.getByRole('dialog', { name: 'Download a model' }) : page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await shot('download-prompt');
    // Only the chat model: memory fetches its embedder by itself as the agent warms up.
    expect(journey.pulls().filter((r) => r.model === CHAT_MODEL), 'the chat model waits for an answer').toEqual([]);
});
