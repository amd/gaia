// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * A ready, local GAIA with a few chats, answered entirely in the page so the
 * snapshots need no backend and no model. Shapes follow src/types/index.ts and
 * were taken from a real backend's responses.
 */

import type { Page, Route } from '@playwright/test';

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

const SESSIONS = [
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

const MESSAGES = {
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
        },
        {
            id: 's2', name: 'Weekly budget check-in', interval_seconds: 604_800, prompt: 'Compare this week\'s spend to the plan',
            status: 'paused', created_at: iso(60 * 24 * 30), last_run_at: iso(60 * 24 * 3), next_run_at: null,
            last_result: 'September is 4% under plan', run_count: 4, error_count: 0, session_id: null, schedule_config: null,
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
    'GET /chat/permissions': { default_mode: 'ask', sessions: [] },
    [`GET /chat/permissions/${ANSWER_SESSION}`]: { session_id: ANSWER_SESSION, mode: 'ask', grants: [] },
    'GET /tunnel/status': { active: false, url: null, token: null, startedAt: null, error: null, publicIp: null },
    'GET /mcp/status': { servers: [] },
    'GET /schedules': SCHEDULES,
    'GET /documents': DOCUMENTS,
    'GET /agents': { agents: [] },
    'GET /connectors': { connectors: [] },
    'GET /skills': { skills: [] },
    'GET /system/model-server': { reachable: true, error: null, base_url: 'http://127.0.0.1:13305', port: 13305, owner: 'gaia' },
    'GET /lemonade/target': { mode: 'gaia', saved_mode: 'gaia', source: 'saved', base_url: 'http://127.0.0.1:13305', saved_base_url: null, api_key_stored: false, env_override: null },
};

/**
 * Answer every /api call from ROUTES. An unlisted call gets a 501 naming it and
 * is returned in the list, so a screen that starts calling a new endpoint fails
 * its test by name instead of rendering whatever an empty body produces.
 */
/** Server-sent event streams the app subscribes to; each opens quiet. */
const STREAMS = new Set(['GET /sessions/events']);

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
        if (!(key in ROUTES)) {
            unhandled.push(key);
            await route.fulfill({ status: 501, contentType: 'application/json', body: JSON.stringify({ detail: `no fixture for ${key}` }) });
            return;
        }
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ROUTES[key]) });
    });
    return unhandled;
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
export async function withModelPage(page: Page, models: unknown[], plan: unknown, status: unknown = { state: 'idle' }): Promise<void> {
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
                { id: 'fireworks', name: 'Fireworks AI', remote: true, models_discovered: true, key_source: 'stored', key_hint: 'a1b2', env_var: 'FIREWORKS_API_KEY', privacy_notice: 'Chat history is sent to Fireworks AI.' },
            ],
        },
        'GET /providers/fireworks/models': { provider: 'fireworks', models: FIREWORKS_MODELS },
        'GET /spending': { month: '2026-10', total_usd: 1.25, turns: 12, unpriced_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price' },
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
export async function withSettingsPanes(page: Page): Promise<void> {
    const answers: Record<string, unknown> = {
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
        if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

const connector = (id: string, display_name: string, type: string, over: Record<string, unknown> = {}) => ({
    id, display_name, type, icon: null, category: 'general', tier: 'core', description: `Lets GAIA work with ${display_name} on your behalf.`,
    product_url: 'https://example.com', docs_url: null, configured: false, configurable: true, enabled: true, account_id: null,
    default_scopes: [], available_scopes: [], mcp_env_keys: [], oauth_setup_fields: [], supports_device_code: false, ...over,
});

const CONNECTORS = [
    connector('google', 'Google', 'oauth_pkce', { configured: true, account_id: 'someone.with.a.long.address@example.com' }),
    connector('git', 'Git', 'mcp_server', { configured: true }),
    connector('github-mcp', 'GitHub', 'mcp_server', { configured: true, enabled: false, mcp_env_keys: ['GITHUB_TOKEN'] }),
    connector('microsoft', 'Microsoft', 'oauth_pkce', { supports_device_code: true }),
    connector('slack', 'Slack', 'oauth_pkce', {
        configurable: false, config_error: 'No client configured',
        oauth_setup_fields: [{ key: 'client_id', label: 'Client ID', kind: 'text', required: true, placeholder: '1234.5678', help_md: 'From your Slack app.' }],
    }),
    ...['Brave Search', 'Notion', 'Postgres', 'Memory', 'Filesystem'].map((n) =>
        connector(n.toLowerCase().replace(' ', '-'), n, 'mcp_server', { mcp_env_keys: n === 'Brave Search' ? ['BRAVE_API_KEY'] : [] })),
];

/** Settings > Connectors with accounts and servers in each state, or the given rows. */
export async function withConnectors(page: Page, connectors: unknown[] = CONNECTORS): Promise<void> {
    const answers: Record<string, unknown> = {
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
