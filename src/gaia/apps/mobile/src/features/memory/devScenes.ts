// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createFakeClient, type ProfileScript } from '@/api/fake';
import type { GaiaClient, Goal, MemoryActivityDay, MemoryConversation, MemoryKnowledgeEntry, MemoryKnowledgeItem, MemoryStats, MemoryToolStat, MemoryTurn, MemoryUpcomingItem, ProfileStreamEvent } from '@/api/client.types';
import { GaiaApiError, GaiaNetworkError } from '@/api/errors';

export type MemoryScene = 'on' | 'off' | 'loading' | 'failed' | 'actionFails' | 'statsEmpty' | 'statsManyTools' | 'aboutEmpty' | 'aboutFailed' | 'aboutMany' | 'goalsEmpty' | 'goalsFailed' | 'goalsStoreError' | 'knowledgeRich' | 'knowledgeEmpty' | 'knowledgeFailed' | 'knowledgeMany' | 'profileIdle' | 'profileScanning' | 'profileReview' | 'profileSaved' | 'profileInferring' | 'profileInferenceReview' | 'profileFailed' | 'profileOff';

export const SAMPLE_MEMORY_STATS: MemoryStats = {
    knowledge: {
        total: 42, total_retrievals: 118, by_category: { fact: 20, preference: 12, note: 10 }, by_context: { global: 38, work: 4 },
        sensitive_count: 2, entity_count: 7, avg_confidence: 0.82, oldest: '2026-09-01T00:00:00Z', newest: '2026-11-13T00:00:00Z',
    },
    conversations: { total_turns: 310, total_sessions: 24, consolidated_sessions: 20, first_session: '2026-09-01T00:00:00Z', last_session: '2026-11-13T00:00:00Z' },
    tools: { total_calls: 90, unique_tools: 8, overall_success_rate: 0.94, total_errors: 5 },
    temporal: { upcoming_count: 1, overdue_count: 0 },
    db_size_bytes: 1_800_000,
};

const EMPTY_MEMORY_STATS: MemoryStats = {
    knowledge: {
        total: 0, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0, entity_count: 0, avg_confidence: 0, oldest: null, newest: null,
    },
    conversations: { total_turns: 0, total_sessions: 0, first_session: null, last_session: null },
    tools: { total_calls: 0, unique_tools: 0, overall_success_rate: 0, total_errors: 0 },
    temporal: { upcoming_count: 0, overdue_count: 0 },
    db_size_bytes: 0,
};

/** `days` days ending today, with counts that rise and fall. */
function sampleActivity(days: number): MemoryActivityDay[] {
    return Array.from({ length: days }, (_, i) => {
        const date = new Date(Date.now() - (days - 1 - i) * 86_400_000).toISOString().slice(0, 10);
        return { date, conversations: (i * 3) % 5, tool_calls: (i * 7) % 11, knowledge_added: i % 4 === 0 ? 2 : 0, errors: i % 9 === 0 ? 1 : 0 };
    });
}

const hoursFromNow = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();

const SAMPLE_TOOLS: MemoryToolStat[] = [
    { tool_name: 'read_file', total_calls: 48, success_count: 47, failure_count: 1, denied_count: 0, success_rate: 0.98, avg_duration_ms: 42, last_used: hoursFromNow(-2), last_error: null },
    { tool_name: 'web_search', total_calls: 21, success_count: 17, failure_count: 4, denied_count: 1, success_rate: 0.81, avg_duration_ms: 1840, last_used: hoursFromNow(-30), last_error: 'Search provider timed out after 10 s' },
    { tool_name: 'run_shell_command', total_calls: 9, success_count: 5, failure_count: 4, denied_count: 3, success_rate: 0.56, avg_duration_ms: 310, last_used: hoursFromNow(-200), last_error: null },
];

const MANY_TOOLS: MemoryToolStat[] = Array.from({ length: 12 }, (_, i) => ({
    tool_name: `${['search_files', 'read_document', 'summarize_text', 'list_folder'][i % 4]}_${i + 1}`,
    total_calls: 5 + i * 3, success_count: 5 + i * 3, failure_count: 0, denied_count: i % 3, success_rate: 1 - (i % 5) * 0.08, avg_duration_ms: 90 + i * 140,
    last_used: hoursFromNow(-i * 9), last_error: i % 4 === 0 ? 'File not found: C:\\Users\\dana\\Documents\\reports\\q3-final.docx' : null,
}));

const SAMPLE_UPCOMING: MemoryUpcomingItem[] = [
    { id: 'u1', content: 'Send the quarterly report to Dana', category: 'reminder', due_at: hoursFromNow(-26), reminded_at: null, context: 'work', created_at: hoursFromNow(-300) },
    { id: 'u2', content: 'Dentist appointment', category: 'reminder', due_at: hoursFromNow(30), reminded_at: null, context: 'global', created_at: hoursFromNow(-100) },
    { id: 'u3', content: 'Renew the domain name', category: 'reminder', due_at: hoursFromNow(120), reminded_at: null, context: 'global', created_at: hoursFromNow(-50) },
];

const daysAgo = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();

function knowledge(id: string, category: string, content: string, source: string, ageDays: number, extra: Partial<MemoryKnowledgeEntry> = {}): MemoryKnowledgeItem & Partial<MemoryKnowledgeEntry> {
    return { id, category, content, domain: null, source, context: 'global', entity: null, sensitive: false, created_at: daysAgo(ageDays + 1), updated_at: daysAgo(ageDays), due_at: null, ...extra };
}

export const SAMPLE_KNOWLEDGE: (MemoryKnowledgeItem & Partial<MemoryKnowledgeEntry>)[] = [
    knowledge('k1', 'fact', 'I live in Calgary and work from home on Tuesdays and Thursdays.', 'tool', 0),
    knowledge('k2', 'preference', 'Keep answers short, and use metric units. If I ask for a long explanation, give it in steps I can follow one at a time.', 'llm_extract', 1),
    knowledge('k3', 'reminder', 'Send the quarterly report to Dana', 'user', 2, { due_at: hoursFromNow(-26) }),
    knowledge('k4', 'reminder', 'Dentist appointment', 'user', 3, { due_at: hoursFromNow(30) }),
    knowledge('k5', 'fact', 'My passport number is 123456789', 'user', 5, { sensitive: true }),
    knowledge('k6', 'note', 'The garage door code changed in October.', 'consolidation', 12),
    knowledge('k7', 'fact', 'I use a Ryzen AI laptop.', 'discovery', 40),
    knowledge('k8', 'preference', 'Call me Sam.', 'inferred', 90),
];

/** Memories with what the Advanced browser shows: entities, a second context, a superseded one, an error GAIA learned, metadata, long and Arabic text. */
const BROWSER_KNOWLEDGE: (MemoryKnowledgeItem & Partial<MemoryKnowledgeEntry>)[] = [
    ...SAMPLE_KNOWLEDGE,
    knowledge('k9', 'fact', 'Sarah Chen leads the platform team and prefers Thursday meetings.', 'llm_extract', 4, { entity: 'person:sarah_chen', context: 'work', domain: 'work', confidence: 0.91, use_count: 7, last_used: daysAgo(1), metadata: { extracted_from: 'session-4', model: 'Gemma-4-E4B-it-GGUF' } }),
    knowledge('k10', 'error', 'The export tool fails when a file name contains a colon; rename the file first and run it again.', 'error_auto', 6, { confidence: 0.55, has_embedding: false, context: 'work' }),
    knowledge('k11', 'skill', 'To summarize a long PDF, read it in sections of ten pages, then merge the section notes into one summary.', 'tool', 20, { confidence: 0.35, entity: 'skill:summarize' }),
    knowledge('k12', 'preference', 'Use the old report template for the Calgary office.', 'user', 60, { superseded_by: 'k2' }),
    knowledge('k13', 'note', 'أفضل أن تكون الإجابات قصيرة ومباشرة، مع الأرقام بالنظام المتري.', 'user', 8),
];

const BROWSER_CONVERSATIONS: { sessions: MemoryConversation[]; turns: Record<string, MemoryTurn[]> } = {
    sessions: [
        { session_id: 'c1aa11bb22', turn_count: 6, started_at: daysAgo(1), last_activity: daysAgo(0), first_message: 'Can you help me plan the quarterly report for the platform team, including the budget section and a timeline for each milestone?', consolidated: true },
        { session_id: 'c2bb22cc33', turn_count: 1, started_at: daysAgo(3), last_activity: daysAgo(3), first_message: 'What is the weather in Calgary this weekend?' },
        { session_id: 'c3cc33dd44', turn_count: 14, started_at: daysAgo(9), last_activity: daysAgo(8), first_message: 'كيف أنظم ملفات المشروع في مجلدات؟', consolidated: true },
    ],
    turns: {
        c1aa11bb22: [
            { id: 1, session_id: 'c1aa11bb22', role: 'user', content: 'Can you help me plan the quarterly report for the platform team, including the budget section and a timeline for each milestone?', context: 'global', timestamp: daysAgo(1) },
            { id: 2, session_id: 'c1aa11bb22', role: 'assistant', content: 'Yes. Start with a one-page summary, then the budget table, then one short section per milestone with an owner and a date. Do you have last quarter\'s report to reuse?', context: 'global', timestamp: daysAgo(1) },
            { id: 3, session_id: 'c1aa11bb22', role: 'user', content: 'I do. It is in Documents/Reports/q3-final.docx', context: 'global', timestamp: daysAgo(0) },
        ],
        c2bb22cc33: [{ id: 4, session_id: 'c2bb22cc33', role: 'user', content: 'What is the weather in Calgary this weekend?', context: 'global', timestamp: daysAgo(3) }],
        c3cc33dd44: [
            { id: 5, session_id: 'c3cc33dd44', role: 'user', content: 'كيف أنظم ملفات المشروع في مجلدات؟', context: 'global', timestamp: daysAgo(8) },
            { id: 6, session_id: 'c3cc33dd44', role: 'assistant', content: 'يمكنك إنشاء مجلد لكل مرحلة من المشروع، ثم مجلد فرعي للوثائق وآخر للصور.', context: 'global', timestamp: daysAgo(8) },
        ],
    },
};

const MANY_KNOWLEDGE: MemoryKnowledgeItem[] = Array.from({ length: 230 }, (_, i) => knowledge(`m${i}`, ['fact', 'preference', 'note'][i % 3]!, `Memory number ${i + 1}`, 'llm_extract', i % 60));

function knowledgeFor(scene: MemoryScene): (MemoryKnowledgeItem & Partial<MemoryKnowledgeEntry>)[] | Error {
    switch (scene) {
        case 'aboutFailed':
        case 'knowledgeFailed':
            return new GaiaApiError(503, 'The backend is not running');
        case 'aboutEmpty':
        case 'knowledgeEmpty':
            return [];
        case 'aboutMany':
        case 'knowledgeMany':
            return MANY_KNOWLEDGE;
        case 'knowledgeRich':
            return BROWSER_KNOWLEDGE;
        default:
            return SAMPLE_KNOWLEDGE;
    }
}

function goal(id: string, title: string, description: string, status: Goal['status'], extra: Partial<Goal> = {}): Goal {
    return {
        id,
        title,
        description,
        status,
        source: 'user',
        mode_required: 'goal_driven',
        approved_for_auto: status !== 'pending_approval',
        priority: 'medium',
        progress_notes: '',
        created_at: daysAgo(3),
        updated_at: daysAgo(1),
        tasks: [],
        ...extra,
    };
}

function task(id: string, goalId: string, description: string, status: Goal['tasks'][number]['status'], result: string | null = null): Goal['tasks'][number] {
    return { id, goal_id: goalId, description, status, order_index: 0, result, created_at: daysAgo(2), updated_at: daysAgo(1) };
}

export const SAMPLE_GOALS: Goal[] = [
    goal('g1', 'Tidy the inbox', 'Collect the numbers and draft the summary.', 'pending_approval', { source: 'agent_inferred', priority: 'high' }),
    goal('g2', 'Summarize this week\u2019s research notes', 'Read every note saved this week and write one page of what changed.', 'in_progress', {
        source: 'agent_inferred',
        priority: 'high',
        progress_notes: 'Two of three sources read.',
        tasks: [task('t1', 'g2', 'Read the saved notes', 'completed', 'Found 14 notes.'), task('t2', 'g2', 'Write the one-page summary', 'in_progress')],
    }),
    goal('g3', 'Rename the vacation photos', 'Use the date and place in each file name.', 'completed', { tasks: [task('t3', 'g3', 'Rename 212 files', 'completed')] }),
    goal('g4', 'Back up the documents folder', '', 'queued', { priority: 'low' }),
    goal('g5', 'Find the cheapest flight to Lisbon', 'Check three sites for dates in March.', 'failed', { tasks: [task('t4', 'g5', 'Open the booking sites', 'failed', 'The browser tool is switched off.')] }),
    goal('g6', 'Plan the garden', 'Draft a planting calendar.', 'completed'),
];

const finding = (source: string, content: string, confidence = 0.8): ProfileStreamEvent => ({
    type: 'finding',
    source,
    item: { content, category: 'profile', context: 'global', confidence, _source_name: source },
});

const DISCOVERY_SO_FAR: ProfileStreamEvent[] = [
    { type: 'log', message: 'Scanning Installed applications...' },
    finding('installed_apps', 'Uses Visual Studio Code, Firefox and Blender'),
    { type: 'log', message: '  Found 1 item(s)' },
    { type: 'log', message: 'Scanning Git repositories...' },
    finding('git_repos', 'Works in C:\\Users\\dana\\source\\gaia-notes and 3 other repositories', 0.9),
];

const DISCOVERY_DONE: ProfileScript = {
    events: [
        ...DISCOVERY_SO_FAR,
        finding('browser_bookmarks', 'Bookmarks cooking blogs and Calgary transit pages', 0.6),
        finding('shell_config', 'Uses PowerShell 7 with a custom prompt', 0.7),
        { type: 'done', total: 4 },
    ],
};

const INSIGHTS_DONE: ProfileScript = {
    events: [
        { type: 'log', message: 'Calling local LLM (Gemma-4-E4B-it-GGUF) for insights (this may take ~30s)...' },
        { type: 'insight', item: { content: 'Primarily a Python developer with a strong AI focus', confidence: 0.9, domain: 'work' } },
        { type: 'insight', item: { content: 'Enjoys 3D modeling as a hobby', confidence: 0.6, domain: 'personal' } },
        { type: 'done', total: 2 },
    ],
};

const PROFILE_SCRIPTS: Partial<Record<MemoryScene, { discovery?: ProfileScript; inference?: ProfileScript }>> = {
    profileScanning: { discovery: { events: DISCOVERY_SO_FAR, hang: true } },
    profileReview: { discovery: DISCOVERY_DONE },
    profileSaved: { discovery: DISCOVERY_DONE },
    profileInferring: { inference: { events: INSIGHTS_DONE.events.slice(0, 2), hang: true } },
    profileInferenceReview: { inference: INSIGHTS_DONE },
    profileFailed: { discovery: { events: DISCOVERY_SO_FAR.slice(0, 2), error: new GaiaNetworkError('http://192.168.1.20:4200/api/memory/stream-discovery', new Error('Network request failed')) } },
};

/** The Profile setup state a scene starts in: which card runs on its own, and whether the result is saved. */
export function profileSceneStart(scene: MemoryScene): { kind: 'discovery' | 'inference'; save?: boolean } | null {
    if (scene === 'profileScanning' || scene === 'profileReview' || scene === 'profileFailed') return { kind: 'discovery' };
    if (scene === 'profileSaved') return { kind: 'discovery', save: true };
    if (scene === 'profileInferring' || scene === 'profileInferenceReview') return { kind: 'inference' };
    return null;
}

/** A fake PC for the dev gallery route, one per state the screen has to show. */
export function memoryScene(scene: MemoryScene): GaiaClient {
    const empty = scene === 'statsEmpty';
    const client = createFakeClient({
        profileStreams: PROFILE_SCRIPTS[scene] ?? { discovery: DISCOVERY_DONE, inference: INSIGHTS_DONE },
        memorySettings: { memory_enabled: scene !== 'off' && scene !== 'profileOff', mcp_memory_enabled: false, system_discovery_consent: true },
        memoryStats: scene === 'failed' ? [new GaiaApiError(503, 'The backend is not running')] : [empty ? EMPTY_MEMORY_STATS : SAMPLE_MEMORY_STATS],
        memoryActivity: (days) => (empty ? [] : sampleActivity(days)),
        memoryTools: empty ? [] : scene === 'statsManyTools' ? MANY_TOOLS : SAMPLE_TOOLS,
        memoryUpcoming: empty ? [] : SAMPLE_UPCOMING,
        memoryKnowledge: knowledgeFor(scene),
        memoryConversations: scene === 'knowledgeFailed' ? new GaiaApiError(503, 'The backend is not running') : scene === 'knowledgeEmpty' ? { sessions: [], turns: {} } : BROWSER_CONVERSATIONS,
        goals: scene === 'goalsFailed' ? new GaiaApiError(503, 'The backend is not running') : scene === 'goalsEmpty' ? [] : SAMPLE_GOALS,
        goalStoreError: scene === 'goalsStoreError' ? 'The goal list is unreadable: unexpected end of JSON input.' : null,
        embeddingCoverage: { total_items: 42, with_embedding: 36, without_embedding: 6, coverage_pct: 85.7 },
        memoryActionResults: {
            consolidate: scene === 'actionFails' ? new GaiaApiError(500, 'Database is locked') : { status: 'ok', consolidated: 3, extracted_items: 12 },
        },
    });
    return scene === 'loading' ? { ...client, getMemoryStats: () => new Promise<never>(() => undefined) } : client;
}
