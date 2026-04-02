// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for the GAIA memory dashboard endpoints. */

const API_BASE = '/api';

async function memFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${API_BASE}${path}`;
    const init: RequestInit = {
        method,
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
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
    const q = Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&');
    return q ? `?${q}` : '';
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
    category?: string;
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

export function createKnowledge(body: KnowledgeCreateBody) {
    return memFetch<any>('POST', '/memory/knowledge', body);
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
    return memFetch<any>('PUT', `/memory/knowledge/${encodeURIComponent(id)}`, body);
}

export function deleteKnowledge(id: string) {
    return memFetch<any>('DELETE', `/memory/knowledge/${encodeURIComponent(id)}`);
}

// ── Entities & Contexts ─────────────────────────────────────────────────────

export function getEntities() {
    return memFetch<any[]>('GET', '/memory/entities');
}

export function getEntityKnowledge(entity: string) {
    return memFetch<any[]>('GET', `/memory/entities/${encodeURIComponent(entity)}`);
}

export function getContexts() {
    return memFetch<any[]>('GET', '/memory/contexts');
}

// ── Tools ───────────────────────────────────────────────────────────────────

export function getToolSummary() {
    return memFetch<any[]>('GET', '/memory/tools');
}

export function getToolHistory(toolName: string, limit = 50) {
    return memFetch<any[]>('GET', `/memory/tools/${encodeURIComponent(toolName)}/history?limit=${limit}`);
}

export function getRecentErrors(limit = 20) {
    return memFetch<any[]>('GET', `/memory/errors?limit=${limit}`);
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

export function pruneMemory(days = 90) {
    return memFetch<{ tool_history_deleted: number; conversations_deleted: number; knowledge_deleted: number }>(
        'POST', `/memory/prune?days=${days}`
    );
}

export function refreshSystemContext() {
    return memFetch<{ stored: number; skipped: boolean; reason?: string }>(
        'POST', '/memory/refresh-system-context'
    );
}

// ── Settings ─────────────────────────────────────────────────────────────────

export interface MemorySettings {
    memory_enabled: boolean;
    mcp_memory_enabled: boolean;
}

export function getMemorySettings() {
    return memFetch<MemorySettings>('GET', '/memory/settings');
}

export function updateMemorySettings(settings: Partial<MemorySettings>) {
    return memFetch<MemorySettings>('PUT', '/memory/settings', settings);
}

export function clearAllMemory() {
    return memFetch<{ knowledge: number; tool_history: number; conversations: number }>(
        'DELETE', '/memory/all'
    );
}
