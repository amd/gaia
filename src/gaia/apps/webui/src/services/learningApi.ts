// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for nightly learning: settings, adapters, learned items, runs. */

import { getApiBase } from '../utils/apiBase';

const API_BASE = getApiBase();

async function learnFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (method !== 'GET') headers['X-Gaia-UI'] = '1';
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const res = await fetch(`${API_BASE}${path}`, {
        method,
        headers: Object.keys(headers).length > 0 ? headers : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        let detail = text;
        try { detail = JSON.parse(text).detail || text; } catch { /* not JSON: keep the text */ }
        throw new Error(detail || `Learning API error (HTTP ${res.status})`);
    }
    return res.json();
}

export type LearningKind = 'preference' | 'concept' | 'workflow' | 'correction';
export type AdapterStatus = 'candidate' | 'active' | 'archived' | 'rejected';
export type RunStatus = 'running' | 'promoted' | 'rejected' | 'nothing_to_learn' | 'stopped' | 'failed' | 'skipped';

export interface LearningConfig {
    enabled: boolean;
    window_start: string;
    window_end: string;
    min_idle_minutes: number;
    require_ac_power: boolean;
    base_model: string | null;
    keep_adapters: number;
}

export interface ProbeResult {
    question: string;
    kind: 'learned' | 'regression';
    passed: boolean;
    reply: string;
    opportunity: string;
}

export interface Score {
    label: string;
    learned_passed: number;
    learned_total: number;
    regression_passed: number;
    regression_total: number;
    results: ProbeResult[];
}

export interface Adapter {
    id: string;
    base_model: string;
    status: AdapterStatus;
    created_at: string;
    decision?: string;
    size_bytes?: number;
    items?: { id: string; kind: LearningKind; title: string }[];
    metrics?: { steps?: number; seconds?: number; base_eval_loss?: number; eval_loss?: number; peak_gpu_gb?: number };
    scores?: { base?: Score; incumbent?: Score; candidate?: Score };
}

export interface RunProgress { phase?: string; step?: number; total?: number; loss?: number }

export interface Run {
    id: string;
    status: RunStatus;
    phase: string;
    trigger: 'manual' | 'scheduled';
    started_at: string;
    finished_at: string | null;
    summary?: string;
    error?: string | null;
    progress?: RunProgress | null;
    adapter_id?: string;
    opportunities?: { id: string; kind: LearningKind; title: string; error: string | null }[];
}

export interface Toolchain {
    ready: boolean;
    torch: string | null;
    gpu: string | null;
    problems: string[];
}

export interface LearningStatus {
    config: LearningConfig;
    base_model: string;
    base_model_trainable: boolean;
    trainable_models: string[];
    toolchain: Toolchain | null;
    active_adapter: Adapter | null;
    latest_run: Run | null;
}

export interface LearnedItem {
    id: string;
    kind: LearningKind;
    title: string;
    evidence: string[];
    examples: number;
    probes: number;
    first_learned: string;
    updated_at: string;
}

export const getStatus = () => learnFetch<LearningStatus>('GET', '/learning/status');
export const updateConfig = (changes: Partial<LearningConfig>) =>
    learnFetch<LearningConfig>('PUT', '/learning/config', changes);
export const listAdapters = () => learnFetch<Adapter[]>('GET', '/learning/adapters');
export const activateAdapter = (id: string) =>
    learnFetch<Adapter>('POST', `/learning/adapters/${encodeURIComponent(id)}/activate`);
export const deactivateAdapter = () => learnFetch<{ deactivated: string | null }>('POST', '/learning/deactivate');
export const deleteAdapter = (id: string) =>
    learnFetch<{ deleted: string }>('DELETE', `/learning/adapters/${encodeURIComponent(id)}`);
export const listItems = () => learnFetch<LearnedItem[]>('GET', '/learning/items');
export const forgetItem = (id: string) =>
    learnFetch<{ removed: string; adapter_retired: string | null }>('DELETE', `/learning/items/${encodeURIComponent(id)}`);
export const listRuns = (limit = 10) => learnFetch<Run[]>('GET', `/learning/runs?limit=${limit}`);
export const startRun = () => learnFetch<{ started: boolean; pid: number }>('POST', '/learning/run');
export const stopRun = () => learnFetch<{ stopping: boolean }>('POST', '/learning/stop');
