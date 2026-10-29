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

const SYSTEM_STATUS = {
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

/** Pick the theme before the app's first paint, the way a returning user would. */
export async function useTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
    await page.addInitScript((t) => {
        window.localStorage.setItem('gaia-chat-theme', t);
    }, theme);
}
