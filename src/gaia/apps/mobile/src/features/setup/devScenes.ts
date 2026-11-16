// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SetupCatalogEntry, SetupModelProgress, SetupRole, SetupTest } from '@webui/types';

import { createFakeClient, type FakeClientData } from '@/api/fake';
import type { GaiaClient, GpuMemoryStatus, PreflightReport, SetupPlan, SetupRunStatus } from '@/api/client.types';

import type { SetupStage } from './flow';

/** A setup step in one state: the stage's body, the plan screen it opens on arrival, and the fake PC's answers. */
export interface SetupScene {
    stage: SetupStage;
    screen?: 'gpu' | 'key' | 'models';
    data: FakeClientData;
}

const entry = (over: Partial<SetupCatalogEntry> & { id: string }): SetupCatalogEntry => ({
    name: over.id, roles: ['chat'], purpose: 'Answers your chats', if_skipped: null, recipe: 'llamacpp', size_gb: 1, full_size_gb: 1,
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
const NPU = entry({ id: 'gemma4-it-e4b-FLM', name: 'Gemma 4 E4B (NPU)', recipe: 'flm', size_gb: 9.1, full_size_gb: 9.1, need_gb: 10.2 });
const CATALOG = [GEMMA, EMBED, QWEN, NPU, MINE, FLM, SPEECH, VOICE, IMAGES];

const COMPONENTS: NonNullable<SetupPlan['components']> = [
    { id: 'server', version: '10.9.1', size_gb: 0.007, source: 'github.com/lemonade-sdk/lemonade/releases', installed: false },
    { id: 'backend', version: null, size_gb: 0.141, source: 'github.com/ggml-org/llama.cpp', installed: null },
    { id: 'agent', version: null, size_gb: null, source: 'hub.amd-gaia.ai', installed: true },
];

const PLAN: SetupPlan = {
    lemonade: { mode: 'gaia', url: 'http://127.0.0.1:13305/api/v1', reachable: true },
    chat_model: { id: GEMMA_ID, user_set: false, size_gb: 5.97, downloaded: false },
    recommended_model: GEMMA_ID, decided_after_server: false, memory_gb: 16, memory_source: 'AMD iGPU',
    capacity_gb: 16, disk: { path: 'C:\\Users\\you\\.cache\\huggingface\\hub', free_gb: 25.3, source: 'lemonade' },
    download_gb: 6.3, disk_warning: null, skipped_models: [], options: [], models: [], catalog: CATALOG,
    components: COMPONENTS,
    backends: [
        { recipe: 'llamacpp', backend: 'vulkan', state: 'installed', default: true },
        { recipe: 'llamacpp', backend: 'cpu', state: 'installable', default: false },
        { recipe: 'flm', backend: 'npu', state: 'unsupported', default: false },
    ],
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

const PLAN_COLD: SetupPlan = { ...PLAN, lemonade: { mode: 'gaia', url: null, reachable: false }, decided_after_server: true, catalog: [] };
const PLAN_PRESENT: SetupPlan = { ...PLAN, download_gb: 0, catalog: CATALOG.map((e) => (e.planned ? { ...e, downloaded: true, size_gb: 0 } : e)) };
const PLAN_SHORT: SetupPlan = { ...PLAN, disk: { ...PLAN.disk, path: PLAN.disk?.path ?? null, source: 'lemonade', free_gb: 2.1 } };
const PLAN_NPU_NOT_READY: SetupPlan = {
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
const PLAN_NO_VISION: SetupPlan = { ...PLAN, chat_model: { id: MINE.id, user_set: true, size_gb: 0, downloaded: true }, recommended_model: null };
const PLAN_NPU_UNUSABLE: SetupPlan = {
    ...PLAN,
    catalog: CATALOG.map((e) => (e.recipe === 'flm' ? { ...e, fits: false, fit_code: 'npu', fit_reason: 'needs an AMD Ryzen AI NPU and its driver' } : e)),
};
const PLAN_LONG: SetupPlan = {
    ...PLAN,
    chat_model: { id: QWEN_ID, user_set: true, size_gb: 23.3, downloaded: false },
    catalog: CATALOG.map((e) => (e.id === QWEN_ID
        ? { ...e, name: LONG_NAME, planned: true, selected: true, fits: true, need_gb: 14.2, purpose: 'Answers your chats, reads your images and works through long documents', source: 'huggingface.co/amd/a-repository-with-a-very-long-name-that-keeps-on-going/resolve/main' }
        : e)),
};
const PLAN_DONE: SetupPlan = { ...PLAN, catalog: CATALOG.map((e) => (e.gaia_model && !e.support ? e : { ...e, downloaded: true, size_gb: 0 })) };

const GPU: GpuMemoryStatus = {
    applies: true, show_in_setup: true, hidden_reason: null, os: 'windows', method: 'guided',
    method_reason: 'On Windows this is set in AMD Software.', total_gb: 128, gpu_gb: 16, cpu_gb: 112,
    setting: { name: 'Variable Graphics Memory', current_gb: 16, min_gb: 4, max_gb: 96 }, reserve_gb: 16,
    needs_admin: true, needs_restart: true,
    presets: [{ setting_gb: 32, gpu_gb: 32, unlocks: [QWEN_ID] }, { setting_gb: 48, gpu_gb: 48, unlocks: [QWEN_ID] }, { setting_gb: 96, gpu_gb: 96, unlocks: [QWEN_ID] }],
    recommended_setting_gb: 32, models: [], pending: null, revert: { available: false, previous_setting_gb: null },
    guided_steps: ['Open AMD Software.', 'Set Variable Graphics Memory to 30 GiB.'],
};
const GPU_KEEP: GpuMemoryStatus = {
    ...GPU, recommended_setting_gb: null,
    models: [{ id: QWEN_ID, size_gb: 23.3, need_gb: 26.8, default: true, fits_now: true, fits_at_setting_gb: null }],
};
const NO_GPU: GpuMemoryStatus = { applies: false, show_in_setup: false, hidden_reason: 'This PC’s GPU has its own memory.', os: 'windows', method: 'unsupported', method_reason: '' };

const row = (id: string, roles: SetupRole[], state: SetupModelProgress['state'], done: number, total: number, extra: Partial<SetupModelProgress> = {}): SetupModelProgress => ({
    id, roles, state, bytes_done: done, bytes_total: total, speed_bps: 0, eta_s: null, error: null, error_kind: null,
    can_retry: state === 'failed', retrying_until: null, downloaded_before_gb: null, ...extra,
});
const test = (job: SetupTest['job'], state: SetupTest['state'], over: Partial<SetupTest> = {}): SetupTest => ({ job, subject: null, state, error: null, kind: null, seconds: null, ...over });

const RUN: SetupRunStatus = {
    state: 'running', text: 'Downloading the models', chat_model: GEMMA_ID, skip_chat_model: false,
    steps: [], log_tail: [], command: 'gaia init --profile gaia',
    components: { server: 'done', backend: 'active' },
    models: [
        row(EMBED_ID, ['embedding'], 'verifying', 330e6, 330e6),
        row(GEMMA_ID, ['chat', 'vision'], 'downloading', 2e9, 5.97e9),
    ],
    overall: { bytes_done: 2.33e9, bytes_total: 6.3e9, speed_bps: 0, eta_s: null, percent: 36, failed: 0 },
};
const RUN_PARTS: SetupRunStatus = { ...RUN, models: [], overall: undefined, percent: 45, size: '63 MB of 141 MB' };
const RUN_ROW_FAILED: SetupRunStatus = {
    ...RUN,
    components: { server: 'done', backend: 'failed' },
    models: [
        row(EMBED_ID, ['embedding'], 'verifying', 330e6, 330e6),
        row(GEMMA_ID, ['chat', 'vision'], 'failed', 2e9, 5.97e9, { error: 'Connection reset by peer', error_kind: 'network' }),
        row(QWEN_ID, ['chat'], 'downloading', 7.6e9, 23.3e9),
    ],
    overall: { bytes_done: 9.93e9, bytes_total: 29.6e9, speed_bps: 0, eta_s: null, percent: 33, failed: 1 },
};
const DONE_ROWS = [row(EMBED_ID, ['embedding'], 'ready', 330e6, 330e6), row(GEMMA_ID, ['chat', 'vision'], 'verifying', 5.97e9, 5.97e9)];
const RUN_AGENT_COMPONENT_FAILED: SetupRunStatus = {
    ...RUN, state: 'failed',
    components: { server: 'done', backend: 'done', agent: 'failed' },
    component_kinds: { agent: 'transient' },
    error: "`gaia init --profile gaia` failed (exit 1): GAIA 0.25.4rc11 is release candidate v0.25.4-rc11, which installs the GAIA agent built from its own commit. Could not read https://github.com/amd/gaia/releases/download/v0.25.4-rc11/gaia-agent-release.json (tried 3 times): HTTPSConnectionPool(host='github.com', port=443): Read timed out. (read timeout=30)",
    models: DONE_ROWS.map((m) => ({ ...m, state: 'ready' as const })),
    overall: { bytes_done: 6.3e9, bytes_total: 6.3e9, speed_bps: 0, eta_s: null, percent: 100, failed: 0 },
};
const RUN_LONG: SetupRunStatus = {
    ...RUN,
    models: [row(QWEN_ID, ['chat', 'vision'], 'downloading', 7.6e9, 23.3e9)],
    overall: { bytes_done: 7.6e9, bytes_total: 23.3e9, speed_bps: 0, eta_s: null, percent: 32, failed: 0 },
};
const RUN_TESTING: SetupRunStatus = {
    ...RUN, state: 'verifying', models: DONE_ROWS,
    tests: [test('server', 'passed', { seconds: 1 }), test('chat', 'passed', { subject: GEMMA_ID, seconds: 14 }), test('vision', 'running', { subject: GEMMA_ID })],
};
const failedTests = (kind: NonNullable<SetupTest['kind']>, error: string): SetupRunStatus => ({
    ...RUN, state: 'failed', error, models: DONE_ROWS,
    tests: [test('server', 'passed', { seconds: 1 }), test('chat', 'failed', { subject: GEMMA_ID, kind, error })],
});
const LONG_ERROR = 'llama-server stopped with exit code 3221225477 while loading C:\\Users\\you\\.cache\\huggingface\\hub\\models--amd--a-very-long-repository-name\\snapshots\\0123456789abcdef\\model-00001-of-00002.gguf';
const RUN_TEST_LONG: SetupRunStatus = {
    ...failedTests('broken', LONG_ERROR),
    tests: [test('server', 'passed', { seconds: 1 }), test('chat', 'failed', { subject: QWEN_ID, kind: 'broken', error: LONG_ERROR })],
};
const RUN_OPTIONAL_FAILED: SetupRunStatus = {
    ...RUN, state: 'verifying', models: DONE_ROWS,
    tests: [
        test('chat', 'passed', { subject: GEMMA_ID, seconds: 14 }),
        test('speech', 'failed', { subject: SPEECH.id, kind: 'broken', error: 'The test sound came back empty.' }),
        test('agent', 'running'),
    ],
};
const NPU_CHAT_FAILED = "Gemma 4 E4B NPU loads on this PC's NPU, but GAIA's test chat on it got no answer. Test again, or choose another chat model. The setup report has the details.";
const RUN_AGENT_FAILED: SetupRunStatus = {
    ...RUN, state: 'failed', error: NPU_CHAT_FAILED, models: DONE_ROWS,
    tests: [
        test('server', 'passed', { seconds: 1 }),
        test('chat', 'passed', { subject: NPU.id, seconds: 9 }),
        test('agent', 'failed', { subject: NPU.id, kind: 'agent', error: NPU_CHAT_FAILED, seconds: 31 }),
    ],
};
const RUN_READY: SetupRunStatus = { ...RUN, state: 'ready', text: 'GAIA is ready', chat_model: GEMMA_ID, models: DONE_ROWS.map((m) => ({ ...m, state: 'ready' as const })) };

const SERVER_RUNNING: SetupRunStatus = { state: 'running', server_only: true, text: 'Starting the local model server', steps: [], log_tail: [] };
const SERVER_FAILED: SetupRunStatus = {
    state: 'failed', server_only: true, steps: [], log_tail: [],
    error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
    problem: { kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.", start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true },
};

const HARDWARE: PreflightReport = {
    os: 'Windows 11 Pro', detected_platform: 'windows', ram_gb: 128, disk_free_gb: 25, npu_detected: true, gpu_name: 'AMD Radeon 8060S', gpu_vram_gb: 16,
    lemonade_running: true, tier: 'full', recommended_profile: 'gaia', recommended_model: GEMMA_ID, required_disk_gb: 30, required_memory_gb: 8,
    compatible: true, blockers: [], warnings: [],
};
const HARDWARE_BLOCKED: PreflightReport = {
    ...HARDWARE, disk_free_gb: 4, tier: 'insufficient', compatible: false,
    blockers: ['Needs 30 GB of disk; 4 GB free.'], warnings: ['Low memory: close other apps before you start.'],
};

const THEIRS = {
    lemonadeTarget: {
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null, api_key_stored: false, offer: true,
        env_url_ignored: null, gaia_installed: true,
        what_gaia_does: "GAIA downloads and runs its models on your server. It leaves your server's settings and your own models as they are.",
    },
    lemonadeDetect: { found: { url: 'http://localhost:13305/api/v1', version: '2026.44.1', minimum: '10.7.0', status: 'ok' as const, message: '' }, unchecked: [] },
} satisfies FakeClientData;

const providers = (localOnly: boolean): NonNullable<FakeClientData['providers']> => {
    const blocked = localOnly;
    return {
        providers: [
            { id: 'local', name: 'Local', remote: false, privacy_notice: '', configured: true, blocked: false },
            { id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', configured: false, blocked },
            { id: 'amd', name: 'AMD LLM Gateway', remote: true, privacy_notice: '', configured: false, blocked },
        ],
        lemonade_error: null,
    };
};

const COMMON: FakeClientData = {
    setupPlan: PLAN,
    setupStatus: { state: 'idle' },
    gpuMemory: NO_GPU,
    preflight: HARDWARE,
    lemonadeTarget: {
        mode: 'gaia', saved_mode: 'gaia', source: 'config', base_url: null, saved_base_url: null, api_key_stored: false, offer: false,
        env_url_ignored: null, gaia_installed: true, what_gaia_does: '',
    },
    lemonadeDetect: { found: null, unchecked: [] },
    providers: providers(false),
    cloudPrices: {
        fireworks: {
            provider: 'fireworks', source: 'Fireworks list price', note: null,
            models: [{ model: 'qwen', name: 'Qwen3.6', rank: 1, note: null, price: { input_per_million: 0.5, cached_input_per_million: 0.1, output_per_million: 3, source: 'list price' } as never }],
        },
        amd: { provider: 'amd', models: [], source: null, note: 'Prices are set by the organization that runs the gateway.' },
    },
    activeModel: { provider: 'local', model: GEMMA_ID, display_name: 'Gemma', label: 'Gemma', remote: false, is_default: true, downloaded: true },
    start: {
        cards: [
            { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research: ', needs_input: true },
            { skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false },
        ],
        working_folder: { path: '/home/you', name: 'you', gaia_folder: false, choosable: true },
    },
};

type Step = 'check' | 'plan' | 'gpu' | 'models' | 'key' | 'download' | 'test' | 'ready';
const scene = (step: Step, data: FakeClientData = {}): SetupScene => {
    const stage: SetupStage = step === 'gpu' || step === 'models' || step === 'key' ? 'plan' : step;
    return { stage, screen: step === 'gpu' || step === 'models' || step === 'key' ? step : undefined, data };
};

/** Every (step, state) the gallery can show; the key is `step/state`, and `default` is a step's plain state. */
export const SETUP_SCENES: Record<string, SetupScene> = {
    'check/found': scene('check'),
    'check/blocked': scene('check', { preflight: HARDWARE_BLOCKED }),
    'check/failed': scene('check', { setupPlan: PLAN_COLD, setupStatus: SERVER_FAILED }),
    'check/offer': scene('check', THEIRS),
    'check/running': scene('check', { setupPlan: PLAN_COLD, setupStatus: SERVER_RUNNING }),
    'download/agent-failed': scene('download', { setupStatus: RUN_AGENT_COMPONENT_FAILED }),
    'download/bundled': scene('download', {
        setupStatus: RUN_PARTS,
        setupPlan: { ...PLAN, components: COMPONENTS.map((c) => (c.id === 'server' ? { ...c, source: 'the GAIA installer', bundled: true } : c)) },
    }),
    'download/running': scene('download', { setupStatus: RUN }),
    'download/failed': scene('download', { setupStatus: RUN_ROW_FAILED }),
    'download/long': scene('download', { setupStatus: RUN_LONG, setupPlan: PLAN_LONG }),
    'download/parts': scene('download', { setupStatus: RUN_PARTS }),
    'gpu/default': scene('gpu', { gpuMemory: GPU }),
    'gpu/keep': scene('gpu', { gpuMemory: GPU_KEEP }),
    'key/default': scene('key'),
    'models/all-present': scene('models', { setupPlan: PLAN_PRESENT }),
    'models/default': scene('models'),
    'models/disk-short': scene('models', { setupPlan: PLAN_SHORT }),
    'models/long': scene('models', { setupPlan: PLAN_LONG }),
    'models/npu-not-ready': scene('models', { setupPlan: PLAN_NPU_NOT_READY }),
    'models/npu-unusable': scene('models', { setupPlan: PLAN_NPU_UNUSABLE }),
    'models/untested-no-vision': scene('models', { setupPlan: PLAN_NO_VISION }),
    'plan/default': scene('plan'),
    'plan/disk-short': scene('plan', { setupPlan: PLAN_SHORT }),
    'plan/local-only': scene('plan', { providers: providers(true) }),
    'plan/long': scene('plan', { setupPlan: PLAN_LONG }),
    'ready/default': scene('ready', { setupStatus: RUN_READY, setupPlan: PLAN_DONE }),
    'test/failed-agent': scene('test', { setupStatus: RUN_AGENT_FAILED }),
    'test/failed-broken': scene('test', { setupStatus: failedTests('broken', 'The model file looks damaged.') }),
    'test/failed-memory': scene('test', { setupStatus: failedTests('memory', 'Not enough free memory to load the model.') }),
    'test/long': scene('test', { setupStatus: RUN_TEST_LONG, setupPlan: PLAN_LONG }),
    'test/optional-failed': scene('test', { setupStatus: RUN_OPTIONAL_FAILED }),
    'test/running': scene('test', { setupStatus: RUN_TESTING }),
};

/** The fake PC for a scene: the same data on every step, with the scene's own answers over it. */
export function setupSceneClient(found: SetupScene): GaiaClient {
    return createFakeClient({ ...COMMON, ...found.data });
}
