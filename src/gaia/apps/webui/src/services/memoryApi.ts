// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for the GAIA memory dashboard endpoints. */

import { getApiBase } from '../utils/apiBase';

const API_BASE = getApiBase();

async function memFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${API_BASE}${path}`;
    const headers: Record<string, string> = {};
    if (method !== 'GET') headers['X-Gaia-UI'] = '1';
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const init: RequestInit = {
        method,
        headers: Object.keys(headers).length > 0 ? headers : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    };
    const res = await fetch(url, init);
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        let detail = text;
        try { detail = JSON.parse(text).detail || text; } catch { /* noop */ }
        throw new Error(detail || `Memory API error (HTTP ${res.status})`);
    }
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('application/json')) return undefined as T;
    return res.json();
}

function toQuery(params: Record<string, unknown>): string {
    const parts: string[] = [];
    for (const [k, v] of Object.entries(params)) {
        if (v === undefined || v === null || v === '') continue;
        if (Array.isArray(v)) {
            for (const item of v) {
                parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(item))}`);
            }
        } else {
            parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
        }
    }
    return parts.length ? `?${parts.join('&')}` : '';
}

// ── Stats & Activity ────────────────────────────────────────────────────────

export function getMemoryStats() {
    return memFetch<any>('GET', '/memory/stats');
}

export function getMemoryActivity(days = 30) {
    return memFetch<any[]>('GET', `/memory/activity?days=${days}`);
}

// ── Knowledge ───────────────────────────────────────────────────────────────

export interface KnowledgeListParams {
    category?: string | string[];
    context?: string;
    entity?: string;
    sensitive?: boolean;
    include_sensitive?: boolean;
    include_superseded?: boolean;
    search?: string;
    sort_by?: string;
    order?: 'asc' | 'desc';
    offset?: number;
    limit?: number;
}

export function getKnowledge(params: KnowledgeListParams = {}) {
    return memFetch<any>('GET', `/memory/knowledge${toQuery(params as Record<string, unknown>)}`);
}

export interface KnowledgeCreateBody {
    content: string;
    category?: string;
    domain?: string;
    context?: string;
    entity?: string;
    sensitive?: boolean;
    due_at?: string;
}

/** A saved entry; `embedded` is absent when an edit left the content alone. */
export interface KnowledgeSaveResult {
    status: string;
    knowledge_id: string;
    embedded?: boolean;
    embed_error?: string;
}

export function createKnowledge(body: KnowledgeCreateBody) {
    return memFetch<KnowledgeSaveResult>('POST', '/memory/knowledge', body);
}

export interface KnowledgeUpdateBody {
    content?: string;
    category?: string;
    domain?: string;
    context?: string;
    entity?: string;
    sensitive?: boolean;
    due_at?: string;
    reminded_at?: string;
}

export function editKnowledge(id: string, body: KnowledgeUpdateBody) {
    return memFetch<KnowledgeSaveResult>('PUT', `/memory/knowledge/${encodeURIComponent(id)}`, body);
}

export function deleteKnowledge(id: string) {
    return memFetch<any>('DELETE', `/memory/knowledge/${encodeURIComponent(id)}`);
}

// ── Entities & Contexts ─────────────────────────────────────────────────────

export function getEntities() {
    return memFetch<any[]>('GET', '/memory/entities');
}

// ── Tools ───────────────────────────────────────────────────────────────────

export function getToolSummary() {
    return memFetch<any[]>('GET', '/memory/tools');
}

// ── Conversations ───────────────────────────────────────────────────────────

export function getMemoryConversations(limit = 20) {
    return memFetch<any[]>('GET', `/memory/conversations?limit=${limit}`);
}

export function searchMemoryConversations(query: string, limit = 20) {
    return memFetch<any[]>('GET', `/memory/conversations/search?query=${encodeURIComponent(query)}&limit=${limit}`);
}

export function getConversationDetail(sessionId: string) {
    return memFetch<any[]>('GET', `/memory/conversations/${encodeURIComponent(sessionId)}`);
}

// ── Temporal ────────────────────────────────────────────────────────────────

export function getUpcomingItems(days = 7) {
    return memFetch<any[]>('GET', `/memory/upcoming?days=${days}`);
}

// ── Embedding Coverage ──────────────────────────────────────────────────────

export function getEmbeddingCoverage() {
    return memFetch<{ total_items: number; with_embedding: number; without_embedding: number; coverage_pct: number }>(
        'GET', '/memory/embedding-coverage'
    );
}

// ── Maintenance ─────────────────────────────────────────────────────────────

export function consolidateSessions() {
    return memFetch<{ status: string; consolidated: number; extracted_items: number }>(
        'POST', '/memory/consolidate'
    );
}

export function rebuildEmbeddings() {
    return memFetch<{ backfilled: number; total_without: number }>(
        'POST', '/memory/rebuild-embeddings'
    );
}

export function reconcileMemory() {
    return memFetch<{ pairs_checked: number; reinforced: number; contradicted: number; weakened: number; neutral: number }>(
        'POST', '/memory/reconcile'
    );
}

export function rebuildFts() {
    return memFetch<{ status: string }>('POST', '/memory/rebuild-fts');
}

export function refreshSystemContext() {
    return memFetch<{ stored: number; skipped: boolean; reason?: string }>(
        'POST', '/memory/refresh-system-context'
    );
}

// ── Profile Setup ─────────────────────────────────────────────────────────────

export interface DiscoveryFinding {
    content: string;
    category: string;
    context: string;
    sensitive?: boolean;
    confidence: number;
    domain?: string;
    entity?: string;
    _source_name?: string;
}

export interface InferenceInsight {
    content: string;
    confidence: number;
    domain: string;
}

/** Open an EventSource SSE stream for system discovery. */
export function openDiscoveryStream(): EventSource {
    return new EventSource(`${API_BASE}/memory/stream-discovery`);
}

/** Open an EventSource SSE stream for LLM profile inference. */
export function openInferenceStream(includeBrowser: boolean): EventSource {
    return new EventSource(`${API_BASE}/memory/stream-inference?include_browser=${includeBrowser}`);
}

export function commitDiscovery(items: DiscoveryFinding[]) {
    return memFetch<{ stored: number }>('POST', '/memory/commit-discovery', { items });
}

export function commitInference(insights: InferenceInsight[]) {
    return memFetch<{ stored: number }>('POST', '/memory/commit-inference', { insights });
}

// ── Settings ─────────────────────────────────────────────────────────────────

export interface MemorySettings {
    memory_enabled: boolean;
    mcp_memory_enabled: boolean;
    system_discovery_consent: boolean;
}

export function getMemorySettings() {
    return memFetch<MemorySettings>('GET', '/memory/settings');
}

/** Response from PUT /api/memory/settings — includes base settings plus
 *  optional fields when system discovery consent is toggled ON. */
export interface MemorySettingsUpdateResponse extends MemorySettings {
    system_context_refresh?: { stored: number; skipped?: boolean };
    system_context_error?: string;
}

export function updateMemorySettings(settings: Partial<MemorySettings>) {
    return memFetch<MemorySettingsUpdateResponse>('PUT', '/memory/settings', settings);
}

export function clearAllMemory() {
    return memFetch<{ knowledge: number; tool_history: number; conversations: number }>(
        'DELETE', '/memory/all'
    );
}

export function reinitializeMemory() {
    return memFetch<{
        cleared: { knowledge: number; tool_history: number; conversations: number };
        system_context: { stored: number };
    }>('POST', '/memory/reinitialize');
}

// ── Goals & Tasks ────────────────────────────────────────────────────────────

export type GoalStatus =
    | 'pending_approval'
    | 'queued'
    | 'in_progress'
    | 'completed'
    | 'failed'
    | 'rejected'
    | 'cancelled';

export type TaskStatus =
    | 'queued'
    | 'in_progress'
    | 'completed'
    | 'failed'
    | 'blocked'
    | 'cancelled';

export type GoalSource = 'user' | 'agent_inferred' | 'agent_scheduled';
export type AgentMode = 'manual' | 'goal_driven' | 'autonomous';
export type Priority = 'low' | 'medium' | 'high';

export interface GoalTask {
    id: string;
    goal_id: string;
    description: string;
    status: TaskStatus;
    order_index: number;
    result: string | null;
    created_at: string;
    updated_at: string;
}

export interface Goal {
    id: string;
    title: string;
    description: string;
    status: GoalStatus;
    source: GoalSource;
    mode_required: AgentMode;
    approved_for_auto: boolean;
    priority: Priority;
    progress_notes: string;
    created_at: string;
    updated_at: string;
    tasks: GoalTask[];
}

export interface GoalStats {
    goals: Partial<Record<GoalStatus, number>>;
    tasks: Partial<Record<TaskStatus, number>>;
}

export function getGoalStats() {
    return memFetch<GoalStats>('GET', '/goals/stats');
}

export function listGoals(params: {
    status?: GoalStatus;
    source?: GoalSource;
    approved_only?: boolean;
} = {}) {
    return memFetch<{ goals: Goal[]; total: number }>(
        'GET', `/goals${toQuery(params as Record<string, unknown>)}`
    );
}

export function createGoal(body: {
    title: string;
    description: string;
    priority?: Priority;
    mode_required?: AgentMode;
}) {
    return memFetch<Goal>('POST', '/goals', body);
}

export function approveGoal(goalId: string) {
    return memFetch<Goal>('PUT', `/goals/${encodeURIComponent(goalId)}/approve`);
}

export function rejectGoal(goalId: string) {
    return memFetch<Goal>('PUT', `/goals/${encodeURIComponent(goalId)}/reject`);
}

export function cancelGoal(goalId: string) {
    return memFetch<Goal>('PUT', `/goals/${encodeURIComponent(goalId)}/cancel`);
}

export function deleteGoal(goalId: string) {
    return memFetch<void>('DELETE', `/goals/${encodeURIComponent(goalId)}`);
}
