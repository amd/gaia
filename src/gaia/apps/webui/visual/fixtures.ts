// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * A ready, local GAIA with a few chats, answered entirely in the page so the
 * snapshots need no backend and no model. Shapes follow src/types/index.ts and
 * were taken from a real backend's responses.
 */

import type { Locator, Page, Route } from '@playwright/test';

/** Every snapshot sees the same afternoon, so "Today" and relative times hold. */
export const NOW = new Date('2026-10-16T15:00:00Z');

const iso = (minutesAgo: number) => new Date(NOW.getTime() - minutesAgo * 60_000).toISOString();

export const ANSWER_SESSION = 'a1b2c3d4-0000-4000-8000-000000000001';

const session = (id: string, title: string, minutesAgo: number, messages: number) => ({
    id,
    title,
    title_is_custom: false,
    created_at: iso(minutesAgo + 5),
    updated_at: iso(minutesAgo),
    model: 'Gemma-4-E4B-it-GGUF',
    effective_model: 'Gemma-4-E4B-it-GGUF',
    system_prompt: null,
    message_count: messages,
    document_ids: [],
    private: false,
    agent_type: 'gaia',
    device: 'gpu',
    mail_provider: null,
    inference_remote: false,
    inference_provider: 'lemonade',
    inference_provider_name: 'Lemonade on this machine',
    inference_description: 'Using Gemma-4-E4B-it-GGUF on this machine.',
});

export const SESSIONS = [
    session(ANSWER_SESSION, 'Rainier weather and Aug 28 action items', 2, 2),
    session('a1b2c3d4-0000-4000-8000-000000000002', 'Draft volunteer email to the Kestrel trail crew', 20, 4),
    session('a1b2c3d4-0000-4000-8000-000000000003', 'kestrel: fix flaky upload test in CI', 95, 6),
    session('a1b2c3d4-0000-4000-8000-000000000004', 'Summarize Q3 board deck', 60 * 26, 4),
    session('a1b2c3d4-0000-4000-8000-000000000005', 'Compare two lease PDFs', 60 * 30, 2),
];

const ANSWER =
    '**Rainier this weekend:** clear Saturday, 40% chance of rain Sunday afternoon. Highs 58 °F at Paradise, lows near freezing overnight.\n\n' +
    '### Action items from Aug 28\n\n' +
    '1. **Maya**: send the trail-permit renewal to the county by Sep 5.\n' +
    '2. **Devon**: get two quotes for the new bear lockers.\n' +
    '3. **Everyone**: review the volunteer schedule in `schedule-fall.xlsx` before the next meeting.\n\n' +
    'I saved these to `C:\\Users\\me\\Documents\\action-items.md`.';

export const MESSAGES = {
    messages: [
        {
            id: 1,
            session_id: ANSWER_SESSION,
            role: 'user',
            content: 'Check Rainier weather and pull action items from the Aug 28 minutes',
            created_at: iso(3),
            rag_sources: null,
        },
        {
            id: 2,
            session_id: ANSWER_SESSION,
            role: 'assistant',
            content: ANSWER,
            created_at: iso(2),
            rag_sources: null,
            agent_steps: [
                { id: 1, type: 'tool', label: 'Using tool', tool: 'fetch_page', detail: 'go.nps.gov/RainierWeather', result: 'Fetched page', active: false, success: true, latencyMs: 1400 },
                { id: 2, type: 'tool', label: 'Using tool', tool: 'read_file', detail: 'C:\\Users\\me\\Documents\\2025-08-28-minutes.html', result: 'Read 14 KB', active: false, success: true, latencyMs: 300 },
                { id: 3, type: 'tool', label: 'Using tool', tool: 'write_file', detail: 'C:\\Users\\me\\Documents\\action-items.md', result: 'Wrote 412 bytes', active: false, success: true, latencyMs: 40 },
            ],
            inference_stats: { tokens_per_second: 41.2, time_to_first_token: 0.9, output_tokens: 142, input_tokens: 2810 },
            library_notices: [
                'schedule-fall.xlsx was kept in your documents library for this chat.',
                'run.exe couldn\'t be added to your documents (.exe files are not supported).',
            ],
        },
    ],
    total: 2,
};

const SCHEDULES = {
    schedules: [
        {
            id: 's1', name: 'Morning brief', interval_seconds: 86_400, prompt: 'Summarize my unread email and calendar for today',
            status: 'active', created_at: iso(60 * 24 * 9), last_run_at: iso(60 * 7), next_run_at: iso(-60 * 17),
            last_result: '3 new items', run_count: 9, error_count: 0, session_id: null, schedule_config: null,
            last_error: null, description: 'Every day',
            description_parts: { kind: 'every', count: 1, unit: 'day', days: null, time: null, start_hour: null, end_hour: null },
        },
        {
            id: 's2', name: 'Weekly budget check-in', interval_seconds: 604_800, prompt: 'Compare this week\'s spend to the plan',
            status: 'paused', created_at: iso(60 * 24 * 30), last_run_at: iso(60 * 24 * 3), next_run_at: null,
            last_result: 'September is 4% under plan', run_count: 4, error_count: 0, session_id: null, schedule_config: null,
            last_error: null, description: 'Every week',
            description_parts: { kind: 'every', count: 1, unit: 'week', days: null, time: null, start_hour: null, end_hour: null },
        },
    ],
    total: 2,
};

const DOCUMENTS = {
    documents: [
        { id: 'd1', filename: '2025-08-28-minutes.html', filepath: 'C:\\Users\\me\\Documents\\2025-08-28-minutes.html', file_size: 14_336, chunk_count: 12, indexed_at: iso(4), last_accessed_at: iso(2), sessions_using: 1, indexing_status: 'complete' },
        { id: 'd2', filename: 'q3-board.pdf', filepath: 'C:\\Users\\me\\Documents\\q3-board.pdf', file_size: 3_250_000, chunk_count: 96, indexed_at: iso(60 * 26), last_accessed_at: null, sessions_using: 0, indexing_status: 'complete' },
    ],
    total: 2,
    total_size_bytes: 3_264_336,
    total_chunks: 108,
};

/** A Strix Halo mid-answer: the GPU busy with the model, the NPU idle. */
const GIB = 1024 ** 3;
/** A minute of jagged, seeded readings around `base`, like a real counter. */
const noisy = (base: number, jitter: number, seed: number) => {
    let x = seed;
    return Array.from({ length: 60 }, () => {
        x = (x * 1103515245 + 12345) % 2 ** 31;
        return Math.max(0, Math.round((base + ((x / 2 ** 31) * 2 - 1) * jitter) * 10) / 10);
    });
};
export const RESOURCES = {
    interval_ms: 1000,
    ready: true,
    cpu: { percent: 20.4, ghz: 4.7, history: noisy(16, 9, 7) },
    memory: { used_bytes: 45 * GIB, total_bytes: 63.6 * GIB, percent: 70.8, history: noisy(70.6, 0.4, 3) },
    devices: [
        { id: '0x00000000_0x0000d938', name: 'AMD Radeon(TM) 8060S Graphics', kind: 'gpu', percent: 14, memory_used_bytes: 30 * GIB, history: noisy(12, 8, 11) },
        { id: '0x00000000_0x0000f994', name: 'NPU Compute Accelerator Device', kind: 'npu', percent: 0, memory_used_bytes: null, history: noisy(0, 0, 1) },
    ],
    networks: [
        { id: 'net_0006008007000000', name: 'Ethernet 3', description: 'Realtek Gaming 2.5GbE Family Controller', kind: 'ethernet', send_bps: 0, recv_bps: 8000, send_history: noisy(20e3, 20e3, 5), recv_history: noisy(60e3, 55e3, 9) },
    ],
};

export const SYSTEM_STATUS = {
    lemonade_running: true, lemonade_error: null, model_loaded: 'Gemma-4-E4B-it-GGUF', embedding_model_loaded: true,
    disk_space_gb: 1293.2, memory_available_gb: 38.5, initialized: true, version: '0.25.0',
    start_instruction: null, start_command: null, lemonade_version: '10.9.1', model_size_gb: 4.9, model_device: 'gpu',
    model_context_size: 65536, model_labels: ['vision'], gpu_name: 'AMD Radeon 8060S', gpu_vram_gb: 96,
    tokens_per_second: 41.2, time_to_first_token: 0.9, processor_name: 'AMD RYZEN AI MAX+ 395 w/ Radeon 8060S',
    context_size_sufficient: true, model_downloaded: true, default_model_name: 'Gemma-4-E4B-it-GGUF',
    default_model_is_cloud: false, default_model_size_gb: 4.9, lemonade_url: 'http://127.0.0.1:13305',
    expected_model_loaded: true, download_progress: null, init_state: 'ready',
    init_tasks: [
        { name: 'Checking LLM server', status: 'done' },
        { name: 'Loading ML libraries', status: 'done' },
        { name: 'Loading AI model', status: 'done' },
    ],
    warm_up: { state: 'done', elapsed_seconds: 2.0, prompt_tokens: 1800, cached_tokens: 1800, error: null },
    detected_devices: ['gpu'], active_profile: 'chat', config_error: null, probe_warnings: [],
    data_dir: 'C:\\Users\\you\\.gaia\\chat',
};

const ROUTES: Record<string, unknown> = {
    'GET /sessions': { sessions: SESSIONS, total: SESSIONS.length },
    [`GET /sessions/${ANSWER_SESSION}/messages`]: MESSAGES,
    'GET /system/status': SYSTEM_STATUS,
    'GET /system/warm-up': SYSTEM_STATUS.warm_up,
    'POST /system/warm-up': SYSTEM_STATUS.warm_up,
    'GET /setup/check': { ready: true, steps: [] },
    'GET /setup/status': { state: 'idle' },
    'GET /setup/needs/chat': { job: 'chat', needed: false, model: null, disk_free_gb: null },
    'GET /settings': { custom_model: null, model_status: null, context_size: null, agent_mode: 'goal_driven', dynamic_tools: false, dynamic_tools_locked: false },
    'GET /providers': {
        providers: [
            { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC. Nothing leaves your machine.' },
            { id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: 'Chat history is sent to Fireworks AI.' },
        ],
    },
    'GET /providers/active': { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma-4-E4B-it-GGUF · local', remote: false, is_default: true, restore: { status: 'none', message: null } },
    'GET /providers/local/models': { models: [{ id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma-4-E4B-it-GGUF', downloaded: true, loaded: true, size_gb: 4.9 }] },
    'GET /onboarding/start': { cards: [], working_folder: { path: '/home/you/projects/gaia', name: 'gaia', gaia_folder: false } },
    [`GET /sessions/${ANSWER_SESSION}/environment`]: {
        session_id: ANSWER_SESSION,
        folders: [{ path: '/home/you/projects/gaia', kind: 'folder', source: 'working_folder', removable: false }],
        removed: [], internet: true, enforced: true,
    },
    'GET /chat/active': { session_ids: [] },
    'GET /providers/local/status': {
        state: 'loaded', active_model: 'Gemma-4-E4B-it-GGUF', active_display_name: 'Gemma 4 E4B', active_remote: false,
        model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', placement: null, ctx_size: 65536, memory: null, memory_error: null,
        error: null, base_url: 'http://127.0.0.1:13305', owner: 'gaia', busy: false, restarting: false, restart_error: null,
    },
    [`GET /chat/context/${ANSWER_SESSION}`]: { session_id: ANSWER_SESSION, context: null },
    // A model on this PC: the composer shows no spending.
    [`GET /spending/chat/${ANSWER_SESSION}`]: {
        paid: false, model: 'Gemma-4-E4B-it-GGUF', price_source: 'list price',
        chat: { usd: 0, answers: 0, unpriced_answers: 0, items: [] },
        today: { usd: 0, answers: 0, unpriced_answers: 0, chats: 0, since: NOW.toISOString() },
    },
    [`GET /sessions/${ANSWER_SESSION}/changes`]: { turns: [] },
    [`GET /sessions/${ANSWER_SESSION}/changes/total`]: {
        files: [], totals: { files: 0, additions: 0, deletions: 0, uncounted: 0 }, turns: 0, pending: false, untracked: [], incomplete: false,
    },
    [`GET /sessions/${ANSWER_SESSION}/outputs`]: { session_id: ANSWER_SESSION, items: [] },
    [`GET /sessions/${ANSWER_SESSION}/capabilities`]: {
        session_id: ANSWER_SESSION, agent_type: 'gaia', skills: [],
        tools: [
            { name: 'read_file', description: 'Read a file from disk.', category: 'file_io' },
            { name: 'fetch_page', description: 'Fetch a web page.', category: 'browser' },
        ],
    },
    'GET /chat/permissions': { default_mode: 'ask', sessions: [] },
    [`GET /chat/permissions/${ANSWER_SESSION}`]: { session_id: ANSWER_SESSION, mode: 'ask', grants: [] },
    'GET /tunnel/status': { active: false, url: null, token: null, startedAt: null, error: null, publicIp: null },
    'GET /mcp/status': { servers: [] },
    'GET /schedules': SCHEDULES,
    'GET /goals/health': { error: null },
    'GET /documents': DOCUMENTS,
    // Installed, so no screen shows the "GAIA agent isn't installed" notice unless its test asks.
    'GET /agents': { agents: [{ id: 'gaia', name: 'GAIA', description: '', source: 'installed', conversation_starters: [], models: [] }], total: 1 },
    'GET /connectors': { connectors: [] },
    'GET /memory/settings': { memory_enabled: true },
    'GET /skills': { skills: [] },
    'GET /skills/featured': { libraries: [{ id: 'pstack', name: 'pstack', url: 'https://github.com/michael-denyer/pstack-claude', description: 'Engineering workflows: design, review, CI fixes, TDD, PR hygiene (MIT)' }] },
    'GET /system/resources': RESOURCES,
    'GET /system/model-server': { reachable: true, error: null, base_url: 'http://127.0.0.1:13305', port: 13305, owner: 'gaia' },
    'GET /speech/model': { downloaded: true, size_gb: 0.3 },
    'GET /spending/prices/fireworks': { provider: 'fireworks', models: [], source: null, note: null },
    'GET /speech/settings': {
        voice: 'bf_emma', speed: 125, auto: true, min_speed: 50, max_speed: 200,
        voices: [
            { id: 'af_bella', name: 'Bella', accent: 'american', gender: 'female', quality: 'A-' },
            { id: 'af_nicole', name: 'Nicole', accent: 'american', gender: 'female', quality: 'B-' },
            { id: 'am_michael', name: 'Michael', accent: 'american', gender: 'male', quality: 'C+' },
            { id: 'bf_emma', name: 'Emma', accent: 'british', gender: 'female', quality: 'B-' },
            { id: 'bm_george', name: 'George', accent: 'british', gender: 'male', quality: 'C' },
        ],
    },
    'GET /lemonade/target': { mode: 'gaia', saved_mode: 'gaia', source: 'saved', base_url: 'http://127.0.0.1:13305', saved_base_url: null, api_key_stored: false, env_override: null },
};

/**
 * Answer every /api call from ROUTES. An unlisted call gets a 501 naming it and
 * is returned in the list, so a screen that starts calling a new endpoint fails
 * its test by name instead of rendering whatever an empty body produces.
 */
/** Server-sent event streams the app subscribes to; each opens quiet. */
const STREAMS = new Set(['GET /sessions/events', 'GET /connectors/events']);

/** A tenth of a second of silence: what POST /speech answers when a reply is read aloud. */
function silentWav(): Buffer {
    const rate = 8000, samples = 800;
    const wav = Buffer.alloc(44 + samples * 2);
    wav.write('RIFF', 0); wav.writeUInt32LE(36 + samples * 2, 4); wav.write('WAVE', 8);
    wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
    wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
    return wav;
}

export async function mockApi(page: Page): Promise<string[]> {
    const unhandled: string[] = [];
    await page.route('**/api/**', async (route: Route) => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace(/^\/api/, '');
        const key = `${route.request().method()} ${path}`;
        if (STREAMS.has(key)) {
            await route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': no events\n\n' });
            return;
        }
        if (key === 'POST /speech') {
            await route.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() });
            return;
        }
        if (!(key in ROUTES)) {
            unhandled.push(key);
            await route.fulfill({ status: 501, contentType: 'application/json', body: JSON.stringify({ detail: `no fixture for ${key}` }) });
            return;
        }
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ROUTES[key]) });
    });
    return unhandled;
}

/** No flagship agent installed, so the app shows its "GAIA agent isn't installed" notice. */
export async function withoutFlagship(page: Page): Promise<void> {
    await page.route('**/api/agents', (route) => route.fulfill({ json: { agents: [], total: 0 } }));
}

const modelRow = (id: string, over: Record<string, unknown>) => ({
    id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null, state: 'downloaded',
    available: true, unavailable_reason: null, fits: true, custom: false,
    placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'expected', warning: null },
    ...over,
});

/** A Strix Halo with models on disk, more to download, an added one, and two it can't run. */
export const MODEL_LIST = [
    modelRow('Gemma-4-E4B-it-GGUF', {
        display_name: 'Gemma 4 E4B (Multimodal)', state: 'loaded', size_gb: 4.9, tokens_per_second: 41.2,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
        evidence: 'Eval: 128 of 218 scenarios passed on this model',
    }),
    modelRow('Qwen3.6-35B-A3B-GGUF', { display_name: 'Qwen3.6 35B A3B (Multimodal)', size_gb: 23.3 }),
    modelRow('gemma4-it-e4b-FLM', {
        display_name: 'Gemma 4 E4B (NPU)', size_gb: 9.1,
        placement: { device: 'npu', backend: 'flm', label: 'NPU · FLM', source: 'expected', warning: null },
    }),
    modelRow('user.Qwen3-4B-Instruct-2507-Q4_K_M-GGUF', {
        display_name: 'Qwen3-4B-Instruct-2507 Q4_K_M with a very long name from Hugging Face', size_gb: 2.5, custom: true,
        source_url: 'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/main',
    }),
    ...['gpt-oss 20B', 'Qwen3-VL-4B Instruct', 'SDXL-Turbo', 'Kokoro TTS', 'Whisper Large v3'].map((name, i) =>
        modelRow(`${name.replace(/ /g, '-')}-GGUF`, { display_name: name, downloaded: false, state: 'not_downloaded', size_gb: 3 + i * 4.5 })),
    modelRow('Qwen3.8-Flash-Next-GGUF', {
        display_name: 'Qwen3.8 Flash Next', downloaded: false, state: 'not_downloaded', size_gb: 82.9, fits: false,
        placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'expected', warning: 'Lemonade reports this model on the CPU, not the GPU.' },
    }),
    modelRow('gemma4-it-e2b-FLM', {
        display_name: 'Gemma 4 E2B (NPU)', downloaded: false, state: 'not_downloaded', available: false, fits: true,
        unavailable_reason: 'needs an AMD Ryzen AI NPU and its driver (No NPU)',
        placement: { device: 'npu', backend: 'flm', label: 'NPU · FLM', source: 'expected', warning: null },
    }),
    modelRow('CPU-Only-GGUF', { display_name: 'A model too big for this PC', downloaded: false, state: 'not_downloaded', available: false, unavailable_reason: 'needs 180 GB of memory; this PC has 96 GB' }),
];

const FIREWORKS_MODELS = [
    { id: 'fireworks.glm-5p3-flash', context_length: 202752, downloaded: true, labels: [], rank: 1, note: 'fast, strong tools', evidence: 'Eval: 171 of 218 scenarios passed',
        price: { input_per_million: 0.4, cached_input_per_million: 0.1, output_per_million: 1.6, source: 'list price' } },
    { id: 'fireworks.deepseek-v4p1', context_length: 131072, downloaded: true, labels: [], rank: 2, note: 'long documents', evidence: null, price: null },
    { id: 'fireworks.other-model', context_length: 65536, downloaded: true, labels: [], rank: null, note: null, evidence: null, price: null },
];

export const MODEL_CONTEXT = {
    model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B (Multimodal)', chosen: null, automatic: 131072, min: 32768,
    max_fit: 131072, options: [32768, 65536, 131072], loaded_ctx: 131072, memory_source: 'AMD iGPU', note: null,
};

export const FAILED_LINK_CHECK = {
    ok: false, url: 'https://huggingface.co/unsloth/flint-GGUF',
    checks: [
        { id: 'link', label: 'Hugging Face link', ok: true, detail: 'unsloth/flint-GGUF' },
        { id: 'architecture', label: 'Supported architecture', ok: false, detail: "This model's architecture, 'flint', is not supported by llama.cpp b10825" },
    ],
    error: "This model's architecture, 'flint', is not supported by llama.cpp b10825",
    warnings: ['This repository has no license file.'], variants: [],
};

/** Settings > Model with `models` on the Local tab; every other answer stays as in ROUTES. */
export async function withModelPage(page: Page, models: unknown[], plan: unknown, status: unknown = { state: 'idle' }, fireworks: Record<string, unknown> = {}, localOnly = false): Promise<void> {
    const answers: Record<string, unknown> = {
        'GET /providers/local/models': { provider: 'local', models, memory_gb: 96, memory_source: 'AMD iGPU', capacity_error: null },
        'GET /providers/local/context': MODEL_CONTEXT,
        'POST /providers/local/custom/check': FAILED_LINK_CHECK,
        'GET /setup/plan': plan,
        'GET /setup/status': status,
        'GET /gpu-memory': { applies: false },
        'GET /providers': {
            providers: [
                { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC. Nothing leaves your machine.' },
                { id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true, key_source: 'stored', key_hint: 'a1b2', env_var: 'FIREWORKS_API_KEY', privacy_notice: 'Chat history is sent to Fireworks AI.', ...(localOnly ? { blocked: true, blocked_reason: 'Local only is on; turn it off in Settings to use cloud models.' } : {}), ...fireworks },
            ],
        },
        'GET /providers/fireworks/models': localOnly
            ? { provider: 'fireworks', models: [], blocked: true, blocked_reason: 'Local only is on; turn it off in Settings to use cloud models.' }
            : { provider: 'fireworks', models: FIREWORKS_MODELS },
        'GET /spending': { month: '2026-10', total_usd: 1.25, turns: 12, unpriced_turns: 0, unreported_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price' },
    };
    await page.route('**/api/**', async (route: Route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

const skill = (name: string, source: string, description: string, over: Record<string, unknown> = {}) => ({
    name, source, description, version: '1.0.0', always_on: false, ...over,
});

/** Busy answers for the other Settings panes: grants, many skills, a failed MCP server. */
export async function withSettingsPanes(page: Page, options: { networkPolicy?: 'cloud_allowed' | 'local_ai_only'; policyNotices?: unknown[] } = {}): Promise<void> {
    const answers: Record<string, unknown> = {
        'GET /settings': {
            custom_model: null, model_status: null, context_size: null, agent_mode: 'goal_driven',
            dynamic_tools: false, dynamic_tools_locked: false,
            network_policy: options.networkPolicy ?? 'cloud_allowed', policy_warnings: [],
            policy_notices: options.policyNotices ?? [],
        },
        'GET /memory/settings': { memory_enabled: true },
        'GET /skills': {
            skills: [
                skill('gh-cli', 'agent-bundled', 'Work with GitHub from the terminal: issues, pull requests and releases.', { always_on: true }),
                ...['web-research', 'pdf-forms', 'jira', 'docker', 'blender', 'summarize'].map((n) => skill(n, 'agent-bundled', `Use ${n} for a task when a request needs it, and report what happened.`)),
                skill('team-standup', 'user', 'Write the team standup from yesterday\'s commits.'),
                skill('deploy-helper', 'claude-project', 'Deploy this project to the staging server.'),
                skill('old-helper', 'claude-import', 'A helper imported from another tool, with a description long enough to wrap onto a second line of the row.'),
            ],
            invalid: { 'C:\\Users\\you\\.gaia\\skills\\broken\\SKILL.md': 'missing name' },
        },
        'GET /chat/permissions': {
            default_mode: 'ask',
            sessions: [
                { session_id: ANSWER_SESSION, mode: 'full_access', grants: [{ key: 'k1', label: 'run_shell: npm test' }, { key: 'k2', label: 'write_file: C:\\Users\\me\\Documents\\action-items.md' }] },
                { session_id: 'a1b2c3d4-0000-4000-8000-000000000002', mode: 'ask', grants: [{ key: 'k3', label: 'fetch_page: go.nps.gov' }] },
            ],
        },
        'GET /mcp/status': { servers: [{ name: 'github', connected: true, tool_count: 12, error: null }, { name: 'calendar', connected: false, tool_count: 0, error: 'Connection refused' }] },
        'GET /settings/undo': { retention_days: 30, max_mb: 500, used_bytes: 12_400_000, turns: 8, path: 'C:\\Users\\you\\.gaia\\undo' },
    };
    await page.route('**/api/**', async (route: Route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key === 'PUT /settings' && options.networkPolicy === 'cloud_allowed') {
            await route.fulfill({ json: {
                custom_model: null, model_status: null, context_size: null, agent_mode: 'goal_driven',
                dynamic_tools: false, dynamic_tools_locked: false, network_policy: 'local_ai_only',
                policy_warnings: [], policy_notices: options.policyNotices ?? [],
            } });
        } else if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

const connector = (id: string, display_name: string, type: string, over: Record<string, unknown> = {}) => ({
    id, display_name, type, icon: null, category: 'general', tier: 'core', description: `Lets GAIA work with ${display_name} on your behalf.`,
    product_url: 'https://example.com', docs_url: null, configured: false, configurable: true, enabled: true, account_id: null,
    default_scopes: [], available_scopes: [], mcp_env_keys: [], oauth_setup_fields: [], supports_device_code: false, ...over,
});

const CONNECTORS = [
    connector('google', 'Google', 'oauth_pkce', {
        configured: true, account_id: 'someone.with.a.long.address@example.com',
        description: 'Connect GAIA to your Google account for Gmail, Calendar, Drive, and more.',
    }),
    connector('git', 'Git', 'mcp_server', { configured: true }),
    connector('github-mcp', 'GitHub', 'mcp_server', { configured: true, enabled: false, mcp_env_keys: ['GITHUB_TOKEN'] }),
    // The catalog's own description, so the Arabic scenes show its translation.
    connector('microsoft', 'Microsoft', 'oauth_pkce', {
        supports_device_code: true,
        description: 'Connect GAIA to your personal Microsoft account (Outlook.com, Hotmail, or Live) for Outlook mail, calendar, OneDrive, and more via Microsoft Graph. For a Microsoft 365 work or school account, use the Microsoft Work or School connector instead.',
    }),
    connector('slack', 'Slack', 'oauth_pkce', {
        configurable: false, config_error: 'No client configured',
        oauth_setup_fields: [{ key: 'client_id', label: 'Client ID', kind: 'text', required: true, placeholder: '1234.5678', help_md: 'From your Slack app.' }],
    }),
    ...['Brave Search', 'Notion', 'Postgres', 'Memory', 'Filesystem'].map((n) =>
        connector(n.toLowerCase().replace(' ', '-'), n, 'mcp_server', { mcp_env_keys: n === 'Brave Search' ? ['BRAVE_API_KEY'] : [] })),
];

/** Settings > Connectors with accounts and servers in each state, or the given rows. */
export async function withConnectors(
    page: Page,
    connectors: unknown[] = CONNECTORS,
    extra: Record<string, unknown> = {},
): Promise<void> {
    const answers: Record<string, unknown> = {
        ...extra,
        'GET /connectors': { connectors },
        'GET /connectors/agent-mcps': {
            agent_mcps: [{ agent_id: 'reports', agent_name: 'Reports', server_name: 'warehouse', command: 'uvx', args: ['warehouse-mcp', '--read-only'], disabled: false, config_path: 'C:\\Users\\you\\.gaia\\agents\\reports\\mcp_servers.json' }],
        },
    };
    await page.route('**/api/**', async (route: Route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key in answers) await route.fulfill({ json: answers[key] });
        else if (/\/grants$/.test(url.pathname)) await route.fulfill({ json: { grants: {} } });
        else if (/\/activations$/.test(url.pathname)) await route.fulfill({ json: { activations: {} } });
        else await route.fallback();
    });
}

const DISCORD_BASE = {
    connected: false, paired: false, running: false, state: 'stopped', detail: '',
    bot_name: '', owner_name: '', autostart: false, always_allowed: [], model: null,
    portal_url: 'https://discord.com/developers/applications',
    bot_page_url: '', invite_url: '', bot_profile_url: '',
    pairing_code: '', pairing_expires_at: 0, discord_installed: true, should_offer_setup: true,
};
const DISCORD_BOT = {
    connected: true, running: true, autostart: true, bot_name: 'GAIA on the office desktop',
    bot_page_url: 'https://discord.com/developers/applications/1300000000000000001/bot',
    invite_url: 'https://discord.com/oauth2/authorize?client_id=1300000000000000001&scope=bot&permissions=0',
    bot_profile_url: 'https://discord.com/users/1300000000000000002',
};

/** Settings > Messaging, one answer per Discord bridge state. */
export const DISCORD_STATES: Record<string, Record<string, unknown>> = {
    none: DISCORD_BASE,
    pairing: { ...DISCORD_BASE, ...DISCORD_BOT, state: 'pairing', pairing_code: 'K7QX4M9P', pairing_expires_at: 1e10, detail: 'DM the code to the bot to pair it with your Discord account.' },
    connected: { ...DISCORD_BASE, ...DISCORD_BOT, state: 'connected', paired: true, owner_name: 'someone_with_a_long_name', model: 'Qwen3.6-35B-A3B-GGUF', always_allowed: ['write_file on files in C:/Users/someone_with_a_long_name/Documents/projects/quarterly-report', 'dir'] },
    crashed: { ...DISCORD_BASE, ...DISCORD_BOT, state: 'stopped', running: false, paired: true, owner_name: 'kalin', detail: 'The Discord bridge stopped unexpectedly.' },
    taken_over: { ...DISCORD_BASE, ...DISCORD_BOT, state: 'taken_over', paired: true, owner_name: 'kalin', detail: 'Another PC is answering this Discord bot.' },
    token_invalid: { ...DISCORD_BASE, ...DISCORD_BOT, state: 'token_invalid', paired: true, owner_name: 'kalin', detail: 'Discord rejected the bot token (HTTP 401). Reset the token in the Developer Portal and paste the new one.' },
};

export async function withDiscord(page: Page, status: Record<string, unknown>): Promise<void> {
    await page.route('**/api/messaging/discord', (route) => route.fulfill({ json: status }));
}

const AT = NOW.getTime() / 1000;
const AUTOMATION = {
    heartbeat: false, paused: false, last_run_at: 0, runs: 0,
    prompt: '', say: '', check: '', quiet: '', days: '', failures: 0,
};

/** Settings > Automations: one of each kind, a paused one and a failing check. */
export const AUTOMATIONS = [
    { ...AUTOMATION, id: 'a1b2c3d4', name: 'Inbox digest', kind: 'summary', every_seconds: 86400, next_at: AT + 61200, prompt: 'Summarize what landed in my inbox overnight and flag anything that needs a reply today.', days: 'mon,tue,wed,thu,fri' },
    { ...AUTOMATION, id: 'e5f6a7b8', name: 'Heartbeat', kind: 'alert', heartbeat: true, every_seconds: 1800, next_at: AT + 1200, prompt: 'Check my calendar, my inbox and CI on main. Only message me if something needs me.', quiet: '22:00-07:00' },
    { ...AUTOMATION, id: 'c1c1c1c1', name: 'Call Sam', kind: 'reminder', next_at: AT + 7200, say: 'Call Sam about the venue deposit.' },
    { ...AUTOMATION, id: 'd2d2d2d2', name: 'Main CI', kind: 'trigger', every_seconds: 900, next_at: AT + 600, prompt: 'Tell me which workflow broke and the first failing step.', check: 'gaia automation ci-status --repo amd/gaia --branch main', failures: 3 },
    { ...AUTOMATION, id: 'f3f3f3f3', name: 'تذكير بموعد الطبيب', kind: 'reminder', paused: true, next_at: AT + 86400, say: 'موعد الطبيب الساعة الرابعة' },
    { ...AUTOMATION, id: 'b4b4b4b4', name: 'Mount Rainier weekend weather window for a two-day climb', kind: 'alert', paused: true, every_seconds: 21600, next_at: AT + 3600, prompt: 'Tell me when a two-day window opens with low wind and no storms.' },
];

export async function withAutomations(page: Page, automations: unknown[], delivers: boolean | null = true): Promise<void> {
    await page.route('**/api/automations', (route) => route.fulfill({ json: { automations, delivers } }));
}

/** The desktop app's backend as services/backend-supervisor.cjs reports it; no apiBase, so requests stay mocked. */
export const BACKEND_RUNNING = {
    status: 'running', port: null, since: null, attempt: 0, maxRestarts: 3,
    nextAttemptAt: null, lastExit: null, lastError: null, apiBase: null,
};

/**
 * The Electron bridges the browser lacks, so desktop-only settings render. `host` is the tray-host check's answer.
 * `statusReadError`: `getStatus` rejects with it, as when the updater's IPC handler is missing.
 */
export async function withDesktopBridges(
    page: Page,
    update: Record<string, unknown> = {},
    host: { available: boolean; reason: string | null } = { available: true, reason: null },
    statusReadError?: string,
): Promise<void> {
    await page.addInitScript(({ state, host, running, readError }) => {
        const tray = { minimizeToTray: true, startOnLogin: false, startMinimized: false };
        const status = {
            status: 'idle', version: null, progress: 0, releaseNotes: null, error: null, currentVersion: '0.25.4',
            pinnedVersion: null, autoUpdate: false, includePrereleases: false, configError: null, ...state,
        };
        const same = async () => status;
        // The app also subscribes to tray and window events; those stay silent here.
        const stub: unknown = new Proxy(function stub() {}, { get: (_, key) => (key === 'then' ? undefined : stub), apply: () => stub });
        const withStubs = (real: object) => new Proxy(real, { get: (t, key) => (key in t ? t[key as keyof typeof t] : stub) });
        Object.assign(window, {
            gaiaAPI: withStubs({
                tray: withStubs({ getConfig: async () => ({ tray, host }), setConfig: async () => ({ tray, host }) }),
                backend: { getState: async () => running, restart: async () => running, onState: () => () => {} },
            }),
            gaiaUpdater: {
                getStatus: readError ? async () => { throw new Error(readError); } : same, check: same, onStatusChange: () => () => {}, listReleases: async () => [], installVersion: same,
                resumeUpdates: same, setAutoUpdate: same, setIncludePrereleases: same, downloadUpdate: same, restart: same,
            },
        });
    }, { state: update, host, running: BACKEND_RUNNING, readError: statusReadError ?? null });
}

/** A desktop app whose backend crashed: `restarting` or, once restarts ran out, `stopped`. */
export async function withCrashedBackend(page: Page, status: 'restarting' | 'stopped', since: number): Promise<void> {
    await page.addInitScript(({ state }) => {
        const stub: unknown = new Proxy(function stub() {}, { get: (_, key) => (key === 'then' ? undefined : stub), apply: () => stub });
        const withStubs = (real: object) => new Proxy(real, { get: (t, key) => (key in t ? t[key as keyof typeof t] : stub) });
        Object.assign(window, {
            gaiaAPI: withStubs({ backend: { getState: async () => state, restart: async () => state, onState: () => () => {} } }),
        });
    }, { state: { ...BACKEND_RUNNING, status, since, attempt: status === 'restarting' ? 2 : 0, lastExit: { code: 1, signal: null } } });
}

/** Pick the theme before the app's first paint, the way a returning user would. */
export async function useTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
    await page.addInitScript((t) => {
        window.localStorage.setItem('gaia-chat-theme', t);
    }, theme);
}

export async function useLocale(page: Page, locale: string): Promise<void> {
    await page.addInitScript((code) => {
        window.localStorage.setItem('gaia-chat-locale', code);
    }, locale);
}

/** A status the running extraction tool's own model calls send, as the backend writes it. */
export const TOOL_STATUS = [
    { name: 'page', phase: 'reading', message: 'Reading page 3 of 12…', en: 'Reading page 3 of 12…', ar: 'جارٍ قراءة الصفحة 3 من 12…' },
    { name: 'restart', phase: 'model_restart', message: 'The model server stopped. GAIA is restarting it…', en: 'The model server stopped. GAIA is restarting it…', ar: 'توقف خادم النموذج. يعيد GAIA تشغيله…' },
] as const;

/** A label's text without the direction isolates the app wraps numbers and glosses in. */
export const shownText = async (label: Locator): Promise<string> =>
    ((await label.textContent()) ?? '').replace(/[\u2066-\u2069]/g, '');

/** POST /api/chat/send starts `extract_document_items`, sends one of its statuses, then holds the turn open. */
export async function holdOnToolStatus(page: Page, status: { phase: string; message: string }): Promise<void> {
    await page.addInitScript(({ phase, message }) => {
        const realFetch = window.fetch.bind(window);
        window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
            const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
            if (!url.includes('/api/chat/send')) return realFetch(input, init);
            const events = [
                { type: 'tool_start', tool: 'extract_document_items', detail: '', narration: 'Extracting document items' },
                { type: 'status', status: 'working', phase, message },
            ];
            const body = new ReadableStream<Uint8Array>({
                start(controller) {
                    for (const e of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
                },
            });
            return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
        };
    }, status);
}

const memoryItem = (id: string, category: string, content: string, over: Record<string, unknown> = {}) => ({
    id, category, content, domain: null, source: 'llm_extract', confidence: 0.8, metadata: null, use_count: 2,
    context: 'global', sensitive: false, entity: null, created_at: iso(60 * 30), updated_at: iso(60 * 5),
    last_used: null, due_at: null, reminded_at: null, superseded_by: null, has_embedding: true, ...over,
});

const MEMORY_ITEMS = [
    memoryItem('k1', 'preference', 'Prefers short answers with the numbers first', { source: 'tool' }),
    memoryItem('k2', 'fact', 'Lives in Calgary and works at a hospital', { entity: 'place:calgary', source: 'user' }),
    memoryItem('k3', 'reminder', 'Dentist appointment', { due_at: '2026-10-23', context: 'personal' }),
    memoryItem('k4', 'fact', 'Bank PIN hint', { sensitive: true }),
    memoryItem('k5', 'note', 'يفضّل الاجتماعات الصباحية', { source: 'consolidation' }),
    memoryItem('k6', 'error', "load_skill: No skill named 'note-taker'.", { source: 'error_auto' }),
    memoryItem('k7', 'error', 'The VPN drops on hotel wifi', { source: 'user' }),
];

const GOAL = {
    id: 'g1', title: 'Ship the Q3 report', description: 'Collect the numbers and draft the summary.', status: 'in_progress',
    source: 'agent_inferred', mode_required: 'manual', approved_for_auto: false, priority: 'high', progress_notes: '',
    created_at: iso(60), updated_at: iso(30),
    tasks: [
        { id: 't1', goal_id: 'g1', description: 'Collect the numbers', status: 'completed', result: 'Done' },
        { id: 't2', goal_id: 'g1', description: 'Draft the summary', status: 'blocked', result: null },
    ],
};

const DISCOVERY_EVENTS = [
    { type: 'log', message: 'Scanning installed apps' },
    { type: 'finding', item: { content: 'Uses VS Code for Python work', category: 'fact', context: 'global', confidence: 0.9, _source_name: 'installed_apps' } },
    { type: 'finding', item: { content: 'Works on the gaia repository', category: 'fact', context: 'work', confidence: 0.7 } },
    { type: 'done', total: 2 },
];

export interface MemoryState {
    enabled?: boolean;
    empty?: boolean;
    /** Every read fails, as when the memory store is locked. */
    failed?: boolean;
    /** More memories than the first page holds. */
    more?: boolean;
}

/** The Memory dashboard's endpoints: a few memories, goals and tools, or empty, off or failing. */
export async function withMemory(page: Page, { enabled = true, empty = false, failed = false, more = false }: MemoryState = {}): Promise<void> {
    const items = empty ? [] : MEMORY_ITEMS;
    const answers: Record<string, unknown> = {
        'GET /memory/settings': { memory_enabled: enabled, mcp_memory_enabled: false, system_discovery_consent: false },
        'GET /memory/stats': {
            knowledge: {
                total: items.length, total_retrievals: empty ? 0 : 9,
                by_category: empty ? {} : { fact: 2, preference: 1, reminder: 1, note: 1 },
                by_context: empty ? {} : { global: 4, personal: 1 }, sensitive_count: empty ? 0 : 1,
                entity_count: empty ? 0 : 1, avg_confidence: 0.8, oldest: null, newest: null,
            },
            conversations: { total_turns: empty ? 0 : 12, total_sessions: empty ? 0 : 3, first_session: null, last_session: null },
            tools: { total_calls: empty ? 0 : 8, unique_tools: empty ? 0 : 2, overall_success_rate: 0.75, total_errors: empty ? 0 : 2 },
            temporal: { upcoming_count: empty ? 0 : 1, overdue_count: 0 },
            embedding: { total_items: items.length, with_embedding: items.length, without_embedding: 0, coverage_pct: 100 },
            db_size_bytes: 9_700_000,
        },
        'GET /memory/activity': empty ? [] : [
            { date: '2026-10-14', conversations: 2, tool_calls: 3, knowledge_added: 1, errors: 0 },
            { date: '2026-10-15', conversations: 1, tool_calls: 5, knowledge_added: 2, errors: 1 },
            { date: '2026-10-16', conversations: 3, tool_calls: 0, knowledge_added: 2, errors: 1 },
        ],
        'GET /memory/knowledge': { items, total: items.length + (more ? 318 : 0), offset: 0, limit: 15 },
        'GET /memory/entities': empty ? [] : [{ entity: 'place:calgary', count: 1, last_updated: iso(60) }],
        'GET /memory/tools': empty ? [] : [
            { tool_name: 'run_shell', total_calls: 6, success_count: 4, failure_count: 2, denied_count: 1, success_rate: 0.67, avg_duration_ms: 1500, last_used: iso(20), last_error: 'Command timed out after 30 s' },
            { tool_name: 'fetch_page', total_calls: 2, success_count: 2, failure_count: 0, denied_count: 0, success_rate: 1, avg_duration_ms: 420, last_used: iso(90), last_error: null },
        ],
        'GET /memory/conversations': empty ? [] : [
            { session_id: ANSWER_SESSION, turn_count: 4, started_at: iso(200), last_activity: iso(120), first_message: 'Summarize the meeting notes', consolidated: true },
            { session_id: 'deadbeef-0000-4000-8000-000000000009', turn_count: 1, started_at: iso(3000), last_activity: iso(3000), first_message: 'تذكّر أنني أسكن في كالغاري', consolidated: false },
        ],
        'GET /memory/upcoming': empty ? [] : [
            { id: 'k3', content: 'Dentist appointment', category: 'reminder', due_at: '2026-10-23T09:00:00Z', reminded_at: null, context: 'personal', created_at: iso(600) },
        ],
        'GET /memory/embedding-coverage': { total_items: items.length, with_embedding: items.length, without_embedding: 0, coverage_pct: 100 },
        'GET /goals': { goals: empty ? [] : [GOAL, { ...GOAL, id: 'g2', title: 'Tidy the inbox', status: 'pending_approval', tasks: [] }], total: empty ? 0 : 2 },
        'GET /goals/stats': { goals: empty ? {} : { in_progress: 1, pending_approval: 1, completed: 3 }, tasks: {} },
    };
    await page.route('**/api/**', async (route: Route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key === 'GET /memory/stream-discovery') {
            const body = DISCOVERY_EVENTS.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
            await route.fulfill({ status: 200, contentType: 'text/event-stream', body });
        } else if (failed && key.startsWith('GET /memory/') && key !== 'GET /memory/settings') {
            await route.fulfill({ status: 500, json: { detail: 'Memory store is locked by another process' } });
        } else if (key in answers) {
            await route.fulfill({ json: answers[key] });
        } else {
            await route.fallback();
        }
    });
}
