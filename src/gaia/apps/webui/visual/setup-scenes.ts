// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * First-run setup, one scene per screen and state, for `rtl.visual.ts` and
 * `screens.visual.ts`. A Strix Halo where Qwen won't fit 16 GB and an FLM model
 * is installed. Shapes follow /api/setup/plan and /api/setup/status.
 */

import { expect, type Page } from '@playwright/test';

const entry = (over: Record<string, unknown> & { id: string }) => ({
    roles: ['chat'], purpose: 'Answers your chats', if_skipped: null, recipe: 'llamacpp', size_gb: 1, full_size_gb: 1,
    partial: false, partial_gb: 0, downloaded: false, in_lemonade: true, recommended: false, planned: false,
    gaia_model: true, selected: false, skipped: false, chat_choice: true, support: 'verified', reads_images: false,
    feature_tier: 'full', source: 'huggingface.co/amd', fits: true, need_gb: 2, capacity_gb: 16, fit_reason: null, ...over,
});

const GEMMA_ID = 'Gemma-4-E4B-it-GGUF';
const QWEN_ID = 'Qwen3.6-35B-A3B-GGUF';
const EMBED_ID = 'user.embeddinggemma-300m-GGUF';
const LONG_NAME = 'Qwen3.6 35B A3B Multimodal Extended Reasoning Preview GGUF Q4_K_M with a 64K context window';

const GEMMA = entry({ id: GEMMA_ID, name: 'Gemma 4 E4B (Multimodal)', roles: ['chat', 'vision'], size_gb: 5.97, full_size_gb: 5.97, recommended: true, planned: true, selected: true, need_gb: 8.3, reads_images: true, feature_tier: 'compact' });
const EMBED = entry({ id: EMBED_ID, name: 'EmbeddingGemma 300M', roles: ['embedding'], purpose: 'Searches your files', size_gb: 0.33, full_size_gb: 0.33, recommended: true, planned: true, selected: true, chat_choice: false, need_gb: 1.3 });
const QWEN = entry({ id: QWEN_ID, name: 'Qwen3.6 35B A3B (Multimodal)', roles: ['chat', 'vision'], size_gb: 23.3, full_size_gb: 23.3, fits: false, need_gb: 26.8, reads_images: true });
const MINE = entry({ id: 'user.my-own-model-GGUF', name: 'My own model', gaia_model: false, downloaded: true, size_gb: 0, support: 'untested', fits: null, need_gb: null, capacity_gb: null });
const FLM = entry({ id: 'gemma4-it-e2b-FLM', name: 'Gemma 4 E2B (NPU)', recipe: 'flm', size_gb: 0, downloaded: true, gaia_model: false, fits: null, need_gb: null, capacity_gb: null });
const SPEECH = entry({ id: 'Whisper-Large-v3-Turbo', name: 'Whisper Large v3 Turbo', roles: ['speech'], purpose: 'Turns speech into text', size_gb: 1.6, support: null, chat_choice: false });
const VOICE = entry({ id: 'kokoro-v1', name: 'Kokoro', roles: ['voice'], purpose: 'Reads answers aloud', size_gb: 0.3, support: null, chat_choice: false });
const IMAGES = entry({ id: 'SDXL-Turbo', name: 'SDXL Turbo', roles: ['image_creation'], purpose: 'Makes pictures', size_gb: 6.9, support: null, chat_choice: false });

const CATALOG = [GEMMA, EMBED, QWEN, MINE, FLM, SPEECH, VOICE, IMAGES];

const COMPONENTS = [
    { id: 'server', version: '10.9.1', size_gb: 0.007, source: 'github.com/lemonade-sdk/lemonade/releases', installed: false },
    { id: 'backend', version: null, size_gb: 0.141, source: 'github.com/ggml-org/llama.cpp', installed: null },
    { id: 'packages', version: null, size_gb: null, source: 'pypi.org', installed: null },
    { id: 'agent', version: null, size_gb: null, source: 'hub.amd-gaia.ai', installed: true },
];

const BACKENDS = [
    { recipe: 'llamacpp', backend: 'vulkan', state: 'installed', default: true },
    { recipe: 'llamacpp', backend: 'cpu', state: 'installable', default: false },
    { recipe: 'flm', backend: 'npu', state: 'unsupported', default: false },
];

const PLAN = {
    profile: 'gaia', lemonade: { mode: 'gaia', url: 'http://127.0.0.1:13305/api/v1', reachable: true },
    chat_model: { id: GEMMA_ID, user_set: false, size_gb: 5.97, downloaded: false },
    recommended_model: GEMMA_ID, decided_after_server: false, memory_gb: 16, memory_source: 'AMD iGPU',
    capacity_gb: 16, disk: { path: 'C:\\Users\\you\\.cache\\huggingface\\hub', free_gb: 25.3, source: 'lemonade' },
    download_gb: 6.3, disk_warning: null, skipped_models: [], options: [], models: [], catalog: CATALOG,
    components: COMPONENTS, backends: BACKENDS,
};

const PLAN_COLD = { ...PLAN, lemonade: { mode: 'gaia', url: null, reachable: false }, decided_after_server: true, catalog: [] };
const PLAN_SHORT = { ...PLAN, disk: { ...PLAN.disk, free_gb: 2.1 } };
const PLAN_NO_VISION = { ...PLAN, chat_model: { id: MINE.id, user_set: true, size_gb: 0, downloaded: true }, recommended_model: null };
const PLAN_TOO_BIG = {
    ...PLAN,
    chat_model: { id: QWEN_ID, user_set: true, size_gb: 23.3, downloaded: false },
    catalog: CATALOG.map((e) => (e.id === QWEN_ID ? { ...e, planned: true, selected: true } : e)),
};
const PLAN_LONG = {
    ...PLAN,
    chat_model: { id: QWEN_ID, user_set: true, size_gb: 23.3, downloaded: false },
    catalog: CATALOG.map((e) => (e.id === QWEN_ID
        ? { ...e, name: LONG_NAME, planned: true, selected: true, fits: true, purpose: 'Answers your chats, reads your images and works through long documents', source: 'huggingface.co/amd/a-repository-with-a-very-long-name-that-keeps-on-going/resolve/main' }
        : e)),
};
const PLAN_DONE = { ...PLAN, catalog: CATALOG.map((e) => (e.gaia_model && !e.support ? e : { ...e, downloaded: true, size_gb: 0 })) };

const NO_GPU = { applies: false, show_in_setup: false, hidden_reason: 'This PC’s GPU has its own memory.', os: 'windows', method: 'unsupported', method_reason: '' };
const GPU = {
    applies: true, show_in_setup: true, hidden_reason: null, os: 'windows', method: 'guided',
    method_reason: 'On Windows this is set in AMD Software.', total_gb: 128, gpu_gb: 16, cpu_gb: 112,
    setting: { name: 'Variable Graphics Memory', current_gb: 16, min_gb: 4, max_gb: 96 }, reserve_gb: 16,
    needs_admin: true, needs_restart: true,
    presets: [{ setting_gb: 32, gpu_gb: 32, unlocks: [QWEN_ID] }, { setting_gb: 48, gpu_gb: 48, unlocks: [QWEN_ID] }],
    recommended_setting_gb: 32, models: [], pending: null, revert: { available: false, previous_setting_gb: null },
    guided_steps: ['Open AMD Software.', 'Set Variable Graphics Memory to 30 GiB.'],
};

const row = (id: string, roles: string[], state: string, done: number, total: number, extra: Record<string, unknown> = {}) => ({
    id, roles, state, bytes_done: done, bytes_total: total, speed_bps: 0, eta_s: null, error: null, error_kind: null,
    can_retry: state === 'failed', retrying_until: null, downloaded_before_gb: null, ...extra,
});

const test = (job: string, state: string, over: Record<string, unknown> = {}) => ({ job, subject: null, state, error: null, kind: null, seconds: null, ...over });

const RUN = {
    state: 'running', text: 'Downloading the models', chat_model: GEMMA_ID, skip_chat_model: false,
    steps: [], log_tail: [], command: 'gaia init --profile gaia',
    components: { server: 'done', backend: 'active' },
    models: [
        row(EMBED_ID, ['embedding'], 'verifying', 330e6, 330e6),
        row(GEMMA_ID, ['chat', 'vision'], 'downloading', 2e9, 5.97e9),
    ],
    overall: { bytes_done: 2.33e9, bytes_total: 6.3e9, speed_bps: 0, eta_s: null, percent: 36, failed: 0 },
};
const RUN_ROW_FAILED = {
    ...RUN,
    components: { server: 'done', backend: 'failed' },
    models: [
        row(EMBED_ID, ['embedding'], 'verifying', 330e6, 330e6),
        row(GEMMA_ID, ['chat', 'vision'], 'failed', 2e9, 5.97e9, { error: 'Connection reset by peer', error_kind: 'network' }),
        row(QWEN_ID, ['chat'], 'downloading', 7.6e9, 23.3e9),
    ],
    overall: { bytes_done: 9.93e9, bytes_total: 29.6e9, speed_bps: 0, eta_s: null, percent: 33, failed: 1 },
};
const RUN_LONG = {
    ...RUN,
    models: [row(QWEN_ID, ['chat', 'vision'], 'downloading', 7.6e9, 23.3e9)],
    overall: { bytes_done: 7.6e9, bytes_total: 23.3e9, speed_bps: 0, eta_s: null, percent: 32, failed: 0 },
};

const DONE_ROWS = [row(EMBED_ID, ['embedding'], 'ready', 330e6, 330e6), row(GEMMA_ID, ['chat', 'vision'], 'verifying', 5.97e9, 5.97e9)];
const RUN_TESTING = {
    ...RUN, state: 'verifying', models: DONE_ROWS,
    tests: [
        test('server', 'passed', { seconds: 1 }),
        test('chat', 'passed', { subject: GEMMA_ID, seconds: 14 }),
        test('vision', 'running', { subject: GEMMA_ID }),
    ],
};
const failedTests = (kind: string, error: string) => ({
    ...RUN, state: 'failed', error, models: DONE_ROWS,
    tests: [test('server', 'passed', { seconds: 1 }), test('chat', 'failed', { subject: GEMMA_ID, kind, error })],
});
const LONG_ERROR = 'llama-server stopped with exit code 3221225477 while loading C:\\Users\\you\\.cache\\huggingface\\hub\\models--amd--a-very-long-repository-name\\snapshots\\0123456789abcdef\\model-00001-of-00002.gguf';
const RUN_TEST_LONG = {
    ...failedTests('broken', LONG_ERROR),
    tests: [test('server', 'passed', { seconds: 1 }), test('chat', 'failed', { subject: QWEN_ID, kind: 'broken', error: LONG_ERROR })],
};
const RUN_OPTIONAL_FAILED = {
    ...RUN, state: 'verifying', models: DONE_ROWS,
    tests: [
        test('chat', 'passed', { subject: GEMMA_ID, seconds: 14 }),
        test('speech', 'failed', { subject: SPEECH.id, kind: 'broken', error: 'The test sound came back empty.' }),
        test('agent', 'running'),
    ],
};
const RUN_READY = { ...RUN, state: 'ready', text: 'GAIA is ready', models: DONE_ROWS.map((m) => ({ ...m, state: 'ready' })) };

const SERVER_RUNNING = { state: 'running', server_only: true, text: 'Starting the local model server', steps: [], log_tail: [] };
const SERVER_FAILED = {
    state: 'failed', server_only: true, steps: [], log_tail: [],
    error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
    problem: {
        kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
        start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true, switch_hint: null,
    },
};

const HARDWARE = { compatible: true, blockers: [], warnings: [], ram_gb: 128, gpu_name: 'AMD Radeon 8060S', npu_detected: true, disk_free_gb: 25, lemonade_running: true };
const HARDWARE_BLOCKED = { ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'], warnings: ['Low memory: close other apps before you start.'] };

const providers = (gatewayReachable: boolean) => ({
    providers: [
        { id: 'local', name: 'Local', remote: false, privacy_notice: '' },
        { id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '' },
        { id: 'amd', name: 'AMD LLM Gateway', remote: true, privacy_notice: '', network: { reachable: gatewayReachable, detail: gatewayReachable ? null : "Can't reach llm-api.amd.com." } },
    ],
    active: { provider: 'local', model: GEMMA_ID, label: 'Gemma', remote: false, is_default: true },
    lemonade_error: null,
});

interface SetupAnswers {
    status?: object;
    plan?: object;
    gpu?: object;
    hardware?: object;
    gatewayReachable?: boolean;
}

/** First-run setup, every call the screens make answered from the backend's shapes. */
export async function withSetup(page: Page, { status = { state: 'idle' }, plan = PLAN, gpu = NO_GPU, hardware = HARDWARE, gatewayReachable = true }: SetupAnswers = {}): Promise<void> {
    const prices = { provider: 'fireworks', models: [{ model: 'qwen', name: 'Qwen3.6', rank: 1, note: null, price: { input_per_million: 0.5, cached_input_per_million: 0.1, output_per_million: 3, source: 'list price' } }], source: 'Fireworks list price', note: null };
    const answers: Record<string, unknown> = {
        'GET /setup/check': { ready: false, stage: 'setup', reasons: [], steps: [{ key: 'server', label: 'Install the local model server', detail: 'about 1 minute' }] },
        'GET /setup/plan': plan,
        'GET /setup/status': status,
        'GET /gpu-memory': gpu,
        'GET /onboarding/preflight': hardware,
        'GET /onboarding/start': {
            cards: [{ skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false }],
            working_folder: { path: '/home/you', name: 'you', gaia_folder: false, choosable: true },
        },
        'GET /lemonade/target': { mode: 'gaia', saved_mode: 'gaia', source: 'saved', base_url: null, saved_base_url: null, api_key_stored: false, offer: false, what_gaia_does: '' },
        'GET /providers': providers(gatewayReachable),
        'GET /spending/prices/fireworks': prices,
        'GET /spending/prices/amd': { provider: 'amd', models: [], source: null, note: 'Prices are set by the organization that runs the gateway.' },
        'GET /memory/settings': { enabled: true },
        'GET /spending': { month: '2026-10', total_usd: 0, turns: 0, unpriced_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price' },
    };
    await page.route('**/api/**', async (route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

const hero = (page: Page) => page.locator('.setup-hero');

export async function toPlan(page: Page): Promise<void> {
    await expect(hero(page)).toBeVisible();
}

export async function toPc(page: Page): Promise<void> {
    await toPlan(page);
    await hero(page).locator('.link-btn').nth(0).click();
    await expect(page.locator('.setup-nav .btn-primary')).toBeVisible();
}

export async function toModels(page: Page): Promise<void> {
    await toPc(page);
    await page.locator('.setup-nav .btn-primary').click();
    await expect(page.locator('.mc-list').first()).toBeVisible();
}

async function toKey(page: Page): Promise<void> {
    await toPlan(page);
    await hero(page).locator('.link-btn').nth(1).click();
    await expect(page.locator('.setup-form')).toBeVisible();
}

export interface SetupScene {
    name: string;
    answers?: SetupAnswers;
    reach: (page: Page) => Promise<void>;
}

/** Every setup screen, in its running, failed and long-content states. */
export const SETUP_SCENES: SetupScene[] = [
    {
        name: 'check-running',
        answers: { plan: PLAN_COLD, status: SERVER_RUNNING },
        reach: async (page) => {
            await expect(page.locator('.setup-result.is-done').first()).toBeVisible();
            await expect(page.locator('.setup-result.is-active').first()).toBeVisible();
        },
    },
    {
        name: 'check-failed',
        answers: { plan: PLAN_COLD, status: SERVER_FAILED },
        reach: (page) => expect(page.locator('.setup-result.is-failed')).toBeVisible(),
    },
    {
        name: 'plan',
        reach: async (page) => {
            await toPlan(page);
            await page.locator('.setup-disclosure summary').click();
            await expect(page.locator('.setup-contents')).toBeVisible();
        },
    },
    {
        name: 'plan-disk-short',
        answers: { plan: PLAN_SHORT },
        reach: async (page) => {
            await toPlan(page);
            await expect(hero(page).locator('.setup-error')).toBeVisible();
        },
    },
    {
        name: 'plan-no-fit-confirm',
        answers: { plan: PLAN_TOO_BIG },
        reach: async (page) => {
            await toPlan(page);
            await hero(page).locator('.btn-primary').click();
            await expect(page.getByRole('alertdialog')).toBeVisible();
        },
    },
    {
        name: 'plan-long',
        answers: { plan: PLAN_LONG },
        reach: async (page) => {
            await toPlan(page);
            await page.locator('.setup-disclosure summary').click();
            await expect(page.locator('.setup-contents')).toBeVisible();
        },
    },
    {
        name: 'pc',
        reach: async (page) => {
            await toPc(page);
            await expect(page.locator('.setup-check')).toBeVisible();
            await expect(page.locator('.setup-rows')).toBeVisible();
        },
    },
    {
        name: 'pc-blocked',
        answers: { hardware: HARDWARE_BLOCKED },
        reach: async (page) => {
            await toPc(page);
            await expect(page.locator('.setup-error')).toBeVisible();
        },
    },
    {
        name: 'gpu',
        answers: { gpu: GPU },
        reach: async (page) => {
            await toPc(page);
            await page.locator('.setup-nav .btn-primary').click();
            await expect(page.locator('.gpu-presets')).toBeVisible();
        },
    },
    {
        name: 'models',
        reach: async (page) => {
            await toModels(page);
            await expect(page.locator('.mc-chip.is-untested')).toBeVisible();
            await expect(page.locator('.mc-warn.is-big').first()).toBeVisible();
            await expect(page.locator('.mc-row.is-off')).toBeVisible();
        },
    },
    {
        name: 'models-untested-no-vision',
        answers: { plan: PLAN_NO_VISION },
        reach: async (page) => {
            await toModels(page);
            await expect(page.locator('p.mc-warn')).toBeVisible();
        },
    },
    {
        name: 'models-disk-short',
        answers: { plan: PLAN_SHORT },
        reach: async (page) => {
            await toModels(page);
            await expect(page.locator('.mc-disk-warn')).toBeVisible();
        },
    },
    {
        name: 'models-long',
        answers: { plan: PLAN_LONG },
        reach: toModels,
    },
    {
        name: 'key',
        reach: async (page) => {
            await toKey(page);
            await expect(page.locator('.setup-key-steps')).toBeVisible();
        },
    },
    {
        name: 'key-gateway-off',
        answers: { gatewayReachable: false },
        reach: async (page) => {
            await toKey(page);
            await expect(page.locator('.setup-choice.is-disabled')).toBeVisible();
        },
    },
    {
        name: 'download-running',
        answers: { status: RUN },
        reach: async (page) => {
            await expect(page.locator('.dl-row').first()).toBeVisible();
            await expect(page.locator('.setup-contents')).toBeVisible();
            await expect(page.getByRole('button', { pressed: false }).first()).toBeVisible();
        },
    },
    {
        name: 'download-failed',
        answers: { status: RUN_ROW_FAILED },
        reach: (page) => expect(page.locator('.dl-row.is-failed')).toBeVisible(),
    },
    {
        name: 'download-long',
        answers: { status: RUN_LONG, plan: PLAN_LONG },
        reach: (page) => expect(page.locator('.dl-row').first()).toBeVisible(),
    },
    {
        name: 'test-running',
        answers: { status: RUN_TESTING },
        reach: (page) => expect(page.locator('.setup-result.is-running')).toBeVisible(),
    },
    {
        name: 'test-optional-failed',
        answers: { status: RUN_OPTIONAL_FAILED },
        reach: (page) => expect(page.locator('.setup-result.is-failed')).toBeVisible(),
    },
    {
        name: 'test-failed-memory',
        answers: { status: failedTests('memory', 'Not enough free memory to load the model.') },
        reach: (page) => expect(page.locator('.setup-failure')).toBeVisible(),
    },
    {
        name: 'test-failed-broken',
        answers: { status: failedTests('broken', 'The model file looks damaged.') },
        reach: (page) => expect(page.locator('.setup-failure .btn-secondary').nth(1)).toBeVisible(),
    },
    {
        name: 'test-long',
        answers: { status: RUN_TEST_LONG, plan: PLAN_LONG },
        reach: (page) => expect(page.locator('.setup-result-error')).toBeVisible(),
    },
    {
        name: 'ready',
        answers: { status: RUN_READY, plan: PLAN_DONE },
        reach: (page) => expect(page.locator('.setup-done')).toBeVisible(),
    },
];

/** The screens whose rows wrap: each must hold together in a narrow window too. */
export const NARROW_SCENES = ['plan', 'pc', 'models', 'models-long', 'key', 'download-failed', 'test-long', 'ready'];

export const scene = (name: string): SetupScene => {
    const found = SETUP_SCENES.find((s) => s.name === name);
    if (!found) throw new Error(`no setup scene named ${name}`);
    return found;
};
