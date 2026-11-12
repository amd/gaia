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

// GAIA's own NPU model, which setup lists on a PC whose NPU holds it.
const NPU = entry({ id: 'gemma4-it-e4b-FLM', name: 'Gemma 4 E4B (NPU)', recipe: 'flm', roles: ['chat'], size_gb: 9.1, full_size_gb: 9.1, fits: true, need_gb: 10.2, reads_images: false });
const CATALOG = [GEMMA, EMBED, QWEN, NPU, MINE, FLM, SPEECH, VOICE, IMAGES];

const COMPONENTS = [
    { id: 'server', version: '10.9.1', size_gb: 0.007, source: 'github.com/lemonade-sdk/lemonade/releases', installed: false },
    { id: 'backend', version: null, size_gb: 0.141, source: 'github.com/ggml-org/llama.cpp', installed: null },
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
    system: {
        gaia_version: '0.25.4', gaia_home: 'C:\\Users\\you\\.gaia', agent_version: '0.3.3', lemonade_version: '2026.44.1',
        os: 'Windows 11 Pro 6.3 (Build 26300)', processor: 'AMD RYZEN AI MAX+ 395 w/ Radeon 8060S', physical_memory: '128.00 GB',
        devices: [
            { kind: 'cpu', name: 'AMD RYZEN AI MAX+ 395 w/ Radeon 8060S', family: 'x86_64', available: true, cores: 16, threads: 32 },
            { kind: 'amd_gpu', name: 'AMD Radeon(TM) 8060S Graphics', family: 'gfx1151', available: true, integrated: true },
            { kind: 'amd_npu', name: 'AMD RYZEN AI MAX+ 395 w/ Radeon 8060S', family: 'XDNA2', available: true },
        ],
    },
};

const PLAN_COLD = { ...PLAN, lemonade: { mode: 'gaia', url: null, reachable: false }, decided_after_server: true, catalog: [] };
const PLAN_SHORT = { ...PLAN, disk: { ...PLAN.disk, free_gb: 2.1 } };
// #6632: Linux with an NPU Lemonade reports, but memlock at 8 MB and no access to the device.
const PLAN_NPU_NOT_READY = {
    ...PLAN,
    npu: {
        present: true,
        ready: false,
        problems: [
            { code: 'access', title: '', fix: '', params: { device: '/dev/accel/accel0', group: 'render' } },
            { code: 'memlock', title: '', fix: '', params: { limit: '8 MB', fix: 'apply' } },
            { code: 'runtime', title: '', fix: '', params: { detail: 'flm-real: error while loading shared libraries: libreadline.so.8: cannot open shared object file' } },
        ],
    },
};
const PLAN_NO_VISION = { ...PLAN, chat_model: { id: MINE.id, user_set: true, size_gb: 0, downloaded: true }, recommended_model: null };
const PLAN_TOO_BIG = {
    ...PLAN,
    chat_model: { id: QWEN_ID, user_set: true, size_gb: 23.3, downloaded: false },
    catalog: CATALOG.map((e) => (e.id === QWEN_ID ? { ...e, planned: true, selected: true } : e)),
};
// Lemonade sees the NPU, but FastFlowLM can't run on this PC (#6632).
const NPU_UNUSABLE = { fits: false, fit_code: 'npu', fit_reason: 'needs an AMD Ryzen AI NPU and its driver' };
const PLAN_NPU_UNUSABLE = { ...PLAN, catalog: CATALOG.map((e) => (e.recipe === 'flm' ? { ...e, ...NPU_UNUSABLE } : e)) };
const PLAN_LONG = {
    ...PLAN,
    chat_model: { id: QWEN_ID, user_set: true, size_gb: 23.3, downloaded: false },
    catalog: CATALOG.map((e) => (e.id === QWEN_ID
        ? { ...e, name: LONG_NAME, planned: true, selected: true, fits: true, need_gb: 14.2, purpose: 'Answers your chats, reads your images and works through long documents', source: 'huggingface.co/amd/a-repository-with-a-very-long-name-that-keeps-on-going/resolve/main' }
        : e)),
};
const PLAN_DONE = { ...PLAN, catalog: CATALOG.map((e) => (e.gaia_model && !e.support ? e : { ...e, downloaded: true, size_gb: 0 })) };

const NO_GPU = { applies: false, show_in_setup: false, hidden_reason: 'This PC’s GPU has its own memory.', os: 'windows', method: 'unsupported', method_reason: '' };
const GPU = {
    applies: true, show_in_setup: true, hidden_reason: null, os: 'windows', method: 'guided',
    method_reason: 'On Windows this is set in AMD Software.', total_gb: 128, gpu_gb: 16, cpu_gb: 112,
    setting: { name: 'Variable Graphics Memory', current_gb: 16, min_gb: 4, max_gb: 96 }, reserve_gb: 16,
    needs_admin: true, needs_restart: true,
    presets: [{ setting_gb: 32, gpu_gb: 32, unlocks: [QWEN_ID] }, { setting_gb: 48, gpu_gb: 48, unlocks: [QWEN_ID] }, { setting_gb: 96, gpu_gb: 96, unlocks: [QWEN_ID] }],
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
    logs: {
        check: ['$ gaia init --profile gaia --server-only', 'Step 1/5: Starting Lemonade Server...', 'server: done', 'The local model server is running'],
        download: ['$ gaia init --profile gaia', `Plan: ${EMBED_ID}, ${GEMMA_ID}`, 'backend: active', `${EMBED_ID}: downloaded`, `Downloading: ${GEMMA_ID} [====------] 34% (2.0 GB/5.97 GB)`],
    },
    models: [
        { ...row(EMBED_ID, ['embedding'], 'verifying', 330e6, 330e6), log: [`Downloading: ${EMBED_ID}`, '[==========] 100% (330 MB/330 MB)', 'Downloaded'] },
        { ...row(GEMMA_ID, ['chat', 'vision'], 'downloading', 2e9, 5.97e9), log: [`Downloading: ${GEMMA_ID}`, 'Fetching unsloth/gemma-4-E4B-it-GGUF:Q4_K_M from huggingface.co', '[===-------] 34% (2.0 GB/5.97 GB)'] },
    ],
    overall: { bytes_done: 2.33e9, bytes_total: 6.3e9, speed_bps: 0, eta_s: null, percent: 36, failed: 0 },
};
// Before any model row: the run is still fetching the parts around the models.
const RUN_PARTS = { ...RUN, models: [], overall: undefined, percent: 45, size: '63 MB of 141 MB' };
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
// The models came down, then reading the candidate's own agent build timed out.
const RUN_AGENT_COMPONENT_FAILED = {
    ...RUN, state: 'failed',
    components: { server: 'done', backend: 'done', agent: 'failed' },
    component_kinds: { agent: 'transient' },
    error: "`gaia init --profile gaia` failed (exit 1): GAIA 0.25.4rc11 is release candidate v0.25.4-rc11, which installs the GAIA agent built from its own commit. Could not read https://github.com/amd/gaia/releases/download/v0.25.4-rc11/gaia-agent-release.json (tried 3 times): HTTPSConnectionPool(host='github.com', port=443): Read timed out. (read timeout=30)",
    models: [row(EMBED_ID, ['embedding'], 'ready', 330e6, 330e6), row(GEMMA_ID, ['chat', 'vision'], 'ready', 5.97e9, 5.97e9)],
    overall: { bytes_done: 6.3e9, bytes_total: 6.3e9, speed_bps: 0, eta_s: null, percent: 100, failed: 0 },
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
const NPU_CHAT_FAILED = "Gemma 4 E4B NPU loads on this PC's NPU, but GAIA's test chat on it got no answer. Test again, or choose another chat model. The setup report has the details.";
const RUN_AGENT_FAILED = {
    ...RUN, state: 'failed', error: NPU_CHAT_FAILED, models: DONE_ROWS,
    tests: [
        test('server', 'passed', { seconds: 1 }),
        test('chat', 'passed', { subject: NPU.id, seconds: 9 }),
        test('agent', 'failed', { subject: NPU.id, kind: 'agent', error: NPU_CHAT_FAILED, seconds: 31 }),
    ],
};
const RUN_READY = { ...RUN, state: 'ready', text: 'GAIA is ready', models: DONE_ROWS.map((m) => ({ ...m, state: 'ready' })) };

const SERVER_RUNNING = { state: 'running', server_only: true, text: 'Starting the local model server', steps: [], log_tail: [] };
const SERVER_FAILED = {
    state: 'failed', server_only: true, steps: [], log_tail: [],
    error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
    problem: {
        kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
        start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true,
    },
};

const HARDWARE = { compatible: true, blockers: [], warnings: [], ram_gb: 128, gpu_name: 'AMD Radeon 8060S', npu_detected: true, disk_free_gb: 25, lemonade_running: true };
const HARDWARE_BLOCKED = { ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'], warnings: ['Low memory: close other apps before you start.'] };

const providers = (gatewayReachable: boolean, envKey: boolean, localOnly = false) => {
    const blocked = localOnly
        ? { blocked: true, blocked_reason: 'Local only is on; turn it off in Settings to use cloud models.' }
        : {};
    return {
        providers: [
            { id: 'local', name: 'Local', remote: false, privacy_notice: '' },
            {
                id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', ...blocked,
                ...(envKey ? { key_source: 'environment', key_hint: '0000', env_var: 'FIREWORKS_API_KEY' } : {}),
            },
            { id: 'amd', name: 'AMD LLM Gateway', remote: true, privacy_notice: '', ...blocked, network: { reachable: gatewayReachable, detail: gatewayReachable ? null : "Can't reach llm-api.amd.com." } },
        ],
        active: { provider: 'local', model: GEMMA_ID, label: 'Gemma', remote: false, is_default: true },
        lemonade_error: null,
    };
};

interface SetupAnswers {
    status?: object;
    plan?: object;
    gpu?: object;
    hardware?: object;
    gatewayReachable?: boolean;
    /** A Fireworks key in the environment, which the Connect step names. */
    envKey?: boolean;
    /** Local only blocks cloud choices while leaving their explanation visible. */
    localOnly?: boolean;
    /** A Lemonade the user already runs, which Check this PC offers. */
    theirs?: boolean;
}

const THEIRS_TARGET = {
    mode: 'gaia', saved_mode: null, source: 'default', base_url: null, saved_base_url: null, api_key_stored: false, offer: true,
    what_gaia_does: "GAIA downloads and runs its models on your server. It leaves your server's settings and your own models as they are.",
};
const THEIRS_FOUND = { found: { url: 'http://localhost:13305/api/v1', version: '2026.44.1', status: 'ok', message: null }, unchecked: [] };

/** First-run setup, every call the screens make answered from the backend's shapes. */
export async function withSetup(page: Page, { status = { state: 'idle' }, plan = PLAN, gpu = NO_GPU, hardware = HARDWARE, gatewayReachable = true, envKey = false, localOnly = false, theirs = false }: SetupAnswers = {}): Promise<void> {
    const prices = { provider: 'fireworks', models: [{ model: 'qwen', name: 'Qwen3.6', rank: 1, note: null, price: { input_per_million: 0.5, cached_input_per_million: 0.1, output_per_million: 3, source: 'list price' } }], source: 'Fireworks list price', note: null };
    const answers: Record<string, unknown> = {
        'GET /setup/check': { ready: false, stage: 'setup', reasons: [], steps: [{ key: 'server', label: 'Install the local model server', detail: 'about 1 minute' }] },
        'GET /setup/plan': plan,
        'GET /setup/status': status,
        'GET /gpu-memory': gpu,
        'GET /onboarding/preflight': hardware,
        'GET /onboarding/start': {
            cards: [
                { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research: ', needs_input: true },
                { skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false },
            ],
            working_folder: { path: '/home/you', name: 'you', gaia_folder: false, choosable: true },
        },
        'GET /lemonade/target': theirs ? THEIRS_TARGET : { mode: 'gaia', saved_mode: 'gaia', source: 'saved', base_url: null, saved_base_url: null, api_key_stored: false, offer: false, what_gaia_does: '' },
        'GET /lemonade/detect': theirs ? THEIRS_FOUND : { found: null, unchecked: [] },
        'GET /providers': providers(gatewayReachable, envKey, localOnly),
        'GET /spending/prices/fireworks': prices,
        'GET /spending/prices/amd': { provider: 'amd', models: [], source: null, note: 'Prices are set by the organization that runs the gateway.' },
        'GET /memory/settings': { enabled: true },
        'GET /spending': { month: '2026-10', total_usd: 0, turns: 0, unpriced_turns: 0, unreported_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price' },
    };
    await page.route('**/api/**', async (route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

const hero = (page: Page) => page.locator('.setup-hero').first();

/** The checks stop on what they found; Continue goes on to the plan. */
export async function toPlan(page: Page): Promise<void> {
    await expect(page.locator('.found')).toBeVisible();
    await page.locator('.setup-footer .btn-primary').click();
    await expect(hero(page)).toBeVisible();
}

export async function toModels(page: Page): Promise<void> {
    await toPlan(page);
    await page.locator('.setup-footer .btn-secondary').click();
    await expect(page.locator('.mc-list').first()).toBeVisible();
}

/** GPU memory, opened from Customize. */
async function toGpu(page: Page): Promise<void> {
    await toModels(page);
    await page.locator('.setup-gpu-option .link-btn').click();
    await expect(page.locator('.gpu-presets')).toBeVisible();
}

async function toKey(page: Page): Promise<void> {
    await toPlan(page);
    await page.locator('.setup-where-card').nth(1).click();
    await page.locator('.setup-footer .btn-primary').click();
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
            // A wait never leaves the bottom bar empty: it says what is going on and offers Cancel.
            await expect(page.locator('.setup-activity')).toBeVisible();
            await expect(page.locator('.setup-footer .btn-secondary')).toBeVisible();
        },
    },
    {
        name: 'check-found',
        reach: (page) => expect(page.locator('.found')).toBeVisible(),
    },
    {
        // Logs open: the page grows past the window, and the scrollbar belongs at the window's edge.
        name: 'check-help-open',
        reach: async (page) => {
            await expect(page.locator('.found')).toBeVisible();
            await page.locator('.setup-help-toggle').click();
            await expect(page.locator('#setup-help')).toBeVisible();
            await page.locator('#setup-help .setup-log-toggle').first().click();
            const edges = await page.evaluate(() => {
                const pane = document.querySelector('.setup-pane')!.getBoundingClientRect();
                const scroller = document.querySelector('.setup-scroll')!;
                const inner = document.querySelector('.setup-inner')!;
                return {
                    gap: Math.abs(pane.right - scroller.getBoundingClientRect().right) + Math.abs(pane.left - scroller.getBoundingClientRect().left),
                    innerScrolls: getComputedStyle(inner).overflowY !== 'visible',
                    gutter: getComputedStyle(scroller).scrollbarGutter,
                };
            });
            expect(edges.gap).toBeLessThan(1);
            expect(edges.innerScrolls).toBe(false);
            expect(edges.gutter).toContain('stable');
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
            // This PC and the cloud are two cards of the same size, not a card and a link.
            const [local, cloud] = await page.locator('.setup-where-card').evaluateAll((cards) => cards.map((c) => c.getBoundingClientRect().width));
            expect(Math.abs(local - cloud)).toBeLessThan(1);
            await page.locator('.setup-disclosure summary').click();
            await expect(page.locator('.setup-contents')).toBeVisible();
        },
    },
    {
        // Moving on focuses the new step's title for screen readers, without drawing a box round it.
        name: 'plan-title-focused',
        reach: async (page) => {
            await toPlan(page);
            const title = page.locator('#setup-title');
            await expect(title).toBeFocused();
            expect(await title.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('none');
            // Its focus mark spans the words, not the whole column.
            const [titleWidth, columnWidth] = await title.evaluate((el) => [el.getBoundingClientRect().width, el.parentElement!.getBoundingClientRect().width]);
            expect(titleWidth).toBeLessThan(columnWidth / 2);
        },
    },
    {
        name: 'plan-cloud-picked',
        reach: async (page) => {
            await toPlan(page);
            await page.locator('.setup-where-card').nth(1).click();
            await expect(page.locator('.setup-where-card.is-active')).toHaveCount(1);
            await expect(page.locator('.setup-where-card').nth(1)).toHaveClass(/is-active/);
        },
    },
    {
        name: 'plan-disk-short',
        answers: { plan: PLAN_SHORT },
        reach: async (page) => {
            await toPlan(page);
            await expect(page.locator('.setup-stage .setup-error')).toBeVisible();
        },
    },
    {
        name: 'plan-no-fit-confirm',
        answers: { plan: PLAN_TOO_BIG },
        reach: async (page) => {
            await toPlan(page);
            await page.locator('.setup-footer .btn-primary').click();
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
        // Every check opened, and All details: the long form of Check this PC.
        name: 'check-details',
        reach: async (page) => {
            await page.locator('.setup-checks-toggle').click();
            await page.locator('.found-more').click();
            await expect(page.locator('.setup-checks-done .setup-rows')).toBeVisible();
            await expect(page.locator('.found-table').last()).toBeVisible();
        },
    },
    {
        // A Lemonade the user already runs: the choice up front, what GAIA does there behind a disclosure.
        name: 'check-offer',
        answers: { theirs: true },
        reach: async (page) => {
            await expect(page.locator('.setup-lemonade')).toBeVisible();
            await page.locator('.setup-offer-more').click();
            await expect(page.locator('#setup-offer-detail')).toBeVisible();
        },
    },
    {
        name: 'check-blocked',
        answers: { hardware: HARDWARE_BLOCKED },
        reach: (page) => expect(page.locator('.found-warnings')).toBeVisible(),
    },
    {
        name: 'gpu',
        answers: { gpu: GPU },
        reach: toGpu,
    },
    {
        // The split already runs the default model: keeping it is picked, and the main button keeps it.
        name: 'gpu-keep',
        answers: { gpu: { ...GPU, recommended_setting_gb: null, models: [{ id: QWEN_ID, size_gb: 23.3, need_gb: 26.8, default: true, fits_now: true, fits_at_setting_gb: null }] } },
        reach: async (page) => {
            await toGpu(page);
            await expect(page.locator('.gpu-preset').first()).toHaveClass(/is-active/);
            await expect(page.locator('.gpu-preset.is-active')).toHaveCount(1);
            await expect(page.locator('.setup-footer .btn-primary')).toHaveCount(1);
            await expect(page.locator('.gpu-unlock:not(.is-keep)')).toHaveCount(0);
        },
    },
    {
        // Max picked, with its warning; the split already runs the default model, so keeping it is the advice.
        name: 'gpu-max',
        answers: { gpu: { ...GPU, recommended_setting_gb: null, models: [{ id: QWEN_ID, size_gb: 23.3, need_gb: 26.8, default: true, fits_now: true, fits_at_setting_gb: null }] } },
        reach: async (page) => {
            await toGpu(page);
            await page.locator('.gpu-preset').last().click();
            await expect(page.locator('.gpu-unlock.is-warn')).toBeVisible();
        },
    },
    {
        name: 'models-npu-unusable',
        answers: { plan: PLAN_NPU_UNUSABLE },
        reach: async (page) => {
            await toModels(page);
            await expect(page.locator('.setup-backend [role="radio"]')).toHaveCount(1);
            await expect(page.locator('.setup-backend .setup-hint')).toBeVisible();
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
        name: 'models-npu',
        reach: async (page) => {
            await toModels(page);
            await page.locator('.setup-backend [role="radio"]').nth(1).click();
            await expect(page.locator('.mc-job p.setup-hint[role="note"]')).toBeVisible();
        },
    },
    {
        name: 'models-npu-not-ready',
        answers: { plan: PLAN_NPU_NOT_READY },
        reach: async (page) => {
            await toModels(page);
            await expect(page.locator('.setup-npu-problems')).toBeVisible();
            await expect(page.locator('.setup-backend [role="radio"]').nth(1)).toBeDisabled();
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
            // Which model GAIA will use is a visible choice, and the key field has a visible name.
            await expect(page.locator('.cloud-prices-list.is-pickable input[type="radio"]:checked')).toHaveCount(1);
            await expect(page.locator('.setup-key-label')).toBeVisible();
        },
    },
    {
        name: 'key-env',
        answers: { envKey: true },
        reach: async (page) => {
            await toKey(page);
            await expect(page.locator('#setup-key-form .setup-hint[role="status"]')).toBeVisible();
        },
    },
    {
        name: 'key-gateway-off',
        answers: { gatewayReachable: false },
        reach: async (page) => {
            await toKey(page);
            // Off AMD's network the gateway isn't offered at all.
            await expect(page.locator('.setup-choice')).toHaveCount(1);
            await expect(page.locator('.setup-choice.is-disabled')).toHaveCount(0);
        },
    },
    {
        // Local only is on: the cloud card stays in place, can't be picked, and says why.
        name: 'plan-local-only',
        answers: { localOnly: true },
        reach: async (page) => {
            await toPlan(page);
            const cloud = page.locator('.setup-where-card').nth(1);
            await expect(cloud).toHaveClass(/is-disabled/);
            await expect(cloud.locator('input[type="radio"]')).toBeDisabled();
            await expect(cloud.locator('.setup-hint')).toHaveText(/\S/);
            await expect(page.locator('.setup-error')).toHaveCount(0);
        },
    },
    {
        name: 'download-running',
        answers: { status: RUN },
        reach: async (page) => {
            await expect(page.locator('.dl-row').first()).toBeVisible();
            await expect(page.locator('.setup-contents')).toBeVisible();
            await expect(page.getByRole('button', { pressed: false }).first()).toBeVisible();
            // A running download keeps a way out in the same bar every step's actions sit in.
            await expect(page.locator('.setup-footer .btn-secondary')).toBeVisible();
        },
    },
    {
        name: 'download-parts',
        answers: { status: RUN_PARTS },
        reach: async (page) => {
            await expect(page.locator('.setup-contents')).toBeVisible();
            await expect(page.locator('.setup-activity progress')).toBeVisible();
            await expect(page.locator('.setup-footer .btn-secondary')).toBeVisible();
        },
    },
    {
        name: 'download-failed',
        answers: { status: RUN_ROW_FAILED },
        reach: (page) => expect(page.locator('.dl-row.is-failed')).toBeVisible(),
    },
    {
        name: 'download-agent-failed',
        answers: { status: RUN_AGENT_COMPONENT_FAILED },
        reach: (page) => expect(page.locator('.setup-content.is-failed .setup-content-failure button')).toBeVisible(),
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
        // A damaged file: Download it again is the bar's main action.
        reach: (page) => expect(page.locator('.setup-footer .btn-primary.setup-wrap-btn')).toBeVisible(),
    },
    {
        // The real chat ran on the chosen NPU model and got no answer: the row names it.
        name: 'test-failed-agent',
        answers: { status: RUN_AGENT_FAILED },
        reach: (page) => expect(page.locator('.setup-result.is-failed .setup-result-model')).toBeVisible(),
    },
    {
        name: 'test-long',
        answers: { status: RUN_TEST_LONG, plan: PLAN_LONG },
        reach: (page) => expect(page.locator('.setup-result-error')).toBeVisible(),
    },
    {
        name: 'ready',
        answers: { status: RUN_READY, plan: PLAN_DONE },
        reach: async (page) => {
            await expect(page.locator('.setup-done')).toBeVisible();
            // A first run has nothing to pick up: the other tasks show, Check in waits.
            await expect(page.locator('.start-card')).toHaveCount(1);
        },
    },
];

/** The screens whose rows wrap: each must hold together in a narrow window too. */
export const NARROW_SCENES = ['plan', 'check-details', 'models', 'models-npu-not-ready', 'models-long', 'key', 'plan-local-only', 'download-running', 'download-failed', 'download-agent-failed', 'test-long', 'ready'];

export const scene = (name: string): SetupScene => {
    const found = SETUP_SCENES.find((s) => s.name === name);
    if (!found) throw new Error(`no setup scene named ${name}`);
    return found;
};
