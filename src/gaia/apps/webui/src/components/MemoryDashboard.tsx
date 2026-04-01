// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
    X, Search, RefreshCw, Plus, Trash2, Pencil, Copy, Clock,
    Brain, MessageSquare, Wrench, TrendingUp, ChevronLeft, ChevronRight,
    AlertTriangle, CheckCircle, Shield, ShieldOff, ChevronDown, Database,
    Zap, GitMerge, Circle,
} from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as memoryApi from '../services/memoryApi';
import { log } from '../utils/logger';
import './MemoryDashboard.css';

// ── Type definitions ─────────────────────────────────────────────────────

interface MemoryStats {
    knowledge: {
        total: number;
        by_category: Record<string, number>;
        by_context: Record<string, number>;
        sensitive_count: number;
        entity_count: number;
        avg_confidence: number;
        oldest: string | null;
        newest: string | null;
    };
    conversations: {
        total_turns: number;
        total_sessions: number;
        consolidated_sessions?: number;
        first_session: string | null;
        last_session: string | null;
    };
    tools: {
        total_calls: number;
        unique_tools: number;
        overall_success_rate: number;
        total_errors: number;
    };
    temporal: {
        upcoming_count: number;
        overdue_count: number;
    };
    embedding?: {
        total_items: number;
        with_embedding: number;
        without_embedding: number;
        coverage_pct: number;
    };
    db_size_bytes: number;
}

interface ActivityDay {
    date: string;
    conversations: number;
    tool_calls: number;
    knowledge_added: number;
    errors: number;
}

interface KnowledgeEntry {
    id: string;
    category: string;
    content: string;
    domain: string | null;
    source: string;
    confidence: number;
    metadata: string | null;
    use_count: number;
    context: string;
    sensitive: boolean;
    entity: string | null;
    created_at: string;
    updated_at: string;
    last_used: string | null;
    due_at: string | null;
    reminded_at: string | null;
    superseded_by: string | null;
    has_embedding?: boolean;
}

interface KnowledgeResponse {
    items: KnowledgeEntry[];
    total: number;
    offset: number;
    limit: number;
}

interface ToolStat {
    tool_name: string;
    total_calls: number;
    success_count: number;
    failure_count: number;
    success_rate: number;
    avg_duration_ms: number;
    last_used: string | null;
    last_error: string | null;
}

interface ConvSession {
    session_id: string;
    turn_count: number;
    started_at: string | null;
    last_activity: string | null;
    first_message: string | null;
    consolidated?: boolean;
}

interface ConvTurn {
    id: number;
    session_id: string;
    role: string;
    content: string;
    context: string;
    timestamp: string;
}

interface TemporalItem {
    id: string;
    content: string;
    category: string;
    due_at: string;
    reminded_at: string | null;
    context: string;
    created_at: string;
}

interface EmbeddingCoverage {
    total_items: number;
    with_embedding: number;
    without_embedding: number;
    coverage_pct: number;
}

interface EntityInfo {
    entity: string;
    count: number;
    last_updated: string;
}

interface ToastMessage {
    id: number;
    text: string;
    type: 'success' | 'error' | 'info';
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatDate(iso: string | null): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch { return '\u2014'; }
}

function formatDateFull(iso: string | null): string {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
    } catch { return ''; }
}

function formatRelativeDate(iso: string | null): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        const now = Date.now();
        const diff = d.getTime() - now;
        const absDiff = Math.abs(diff);
        const mins = Math.floor(absDiff / 60000);
        const hours = Math.floor(absDiff / 3600000);
        const days = Math.floor(absDiff / 86400000);

        if (diff < 0) {
            // Past
            if (mins < 5) return 'just now';
            if (hours === 0) return `${mins}m ago`;
            if (days === 0) return `${hours}h ago`;
            if (days === 1) return 'yesterday';
            if (days < 30) return `${days} days ago`;
            return formatDate(iso);
        } else {
            // Future
            if (mins < 5) return 'now';
            if (hours === 0) return `in ${mins}m`;
            if (days === 0) return `in ${hours}h`;
            if (days === 1) return 'tomorrow';
            if (days < 30) return `in ${days} days`;
            return formatDate(iso);
        }
    } catch { return '\u2014'; }
}

function formatBytes(bytes: number): string {
    if (bytes <= 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDuration(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function isOverdue(dueAt: string): boolean {
    try {
        return new Date(dueAt).getTime() < Date.now();
    } catch { return false; }
}

function confidenceLevel(c: number): 'high' | 'medium' | 'low' {
    if (c >= 0.7) return 'high';
    if (c >= 0.4) return 'medium';
    return 'low';
}

function successRateClass(rate: number): string {
    if (rate >= 0.9) return 'high';
    if (rate >= 0.7) return 'medium';
    return 'low';
}

function formatDateRange(start: string | null, end: string | null): string {
    const s = formatDate(start);
    const e = formatDate(end);
    if (s === '\u2014' && e === '\u2014') return '';
    if (s === e) return s;
    return `${s} \u2013 ${e}`;
}

/** Count today's activity from the timeline data. */
function todayCount(activity: ActivityDay[]): number {
    if (activity.length === 0) return 0;
    const today = new Date().toISOString().slice(0, 10);
    const todayData = activity.find(d => d.date === today);
    if (!todayData) return 0;
    return todayData.knowledge_added;
}

const ALL_CATEGORIES = ['fact', 'preference', 'error', 'skill', 'note', 'reminder'] as const;

// ── Main Component ──────────────────────────────────────────────────────

export function MemoryDashboard() {
    const { setShowMemoryDashboard } = useChatStore();

    // Data state
    const [stats, setStats] = useState<MemoryStats | null>(null);
    const [activity, setActivity] = useState<ActivityDay[]>([]);
    const [knowledge, setKnowledge] = useState<KnowledgeResponse | null>(null);
    const [tools, setTools] = useState<ToolStat[]>([]);
    const [conversations, setConversations] = useState<ConvSession[]>([]);
    const [upcoming, setUpcoming] = useState<TemporalItem[]>([]);
    const [embeddingCoverage, setEmbeddingCoverage] = useState<EmbeddingCoverage | null>(null);
    const [entities, setEntities] = useState<EntityInfo[]>([]);

    // UI state
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [hoveredBar, setHoveredBar] = useState<number | null>(null);
    const [showMaintenanceMenu, setShowMaintenanceMenu] = useState(false);
    const [expandedRowId, setExpandedRowId] = useState<string | null>(null);
    const [toasts, setToasts] = useState<ToastMessage[]>([]);
    const toastIdRef = useRef(0);

    // Knowledge filters
    const [kCategory, setKCategory] = useState('');
    const [kContext, setKContext] = useState('');
    const [kEntity, setKEntity] = useState('');
    const [kSearch, setKSearch] = useState('');
    const [kSort, setKSort] = useState('updated_at');
    const [kOrder, setKOrder] = useState<'desc' | 'asc'>('desc');
    const [kOffset, setKOffset] = useState(0);
    const [includeSuperseded, setIncludeSuperseded] = useState(false);
    const kLimit = 15;

    // Conversation search
    const [convSearch, setConvSearch] = useState('');
    const [convSearchResults, setConvSearchResults] = useState<ConvTurn[] | null>(null);
    const convSearchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [convDetail, setConvDetail] = useState<{ sessionId: string; turns: ConvTurn[] } | null>(null);

    // Knowledge CRUD
    const [showAddForm, setShowAddForm] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [formData, setFormData] = useState({
        content: '', category: 'fact', domain: '', context: 'global',
        entity: '', sensitive: false, due_at: '',
    });

    // Revealed sensitive items
    const [revealedIds, setRevealedIds] = useState<Set<string>>(new Set());

    // Maintenance state
    const [consolidating, setConsolidating] = useState(false);
    const [rebuildingEmbeddings, setRebuildingEmbeddings] = useState(false);
    const [reconciling, setReconciling] = useState(false);
    const [rebuildingFts, setRebuildingFts] = useState(false);
    const [searchPending, setSearchPending] = useState(false);

    const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const maintenanceRef = useRef<HTMLDivElement>(null);

    // ── Toast helpers ──────────────────────────────────────────────────

    const showToast = useCallback((text: string, type: ToastMessage['type'] = 'info') => {
        const id = ++toastIdRef.current;
        setToasts(prev => [...prev, { id, text, type }]);
        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 4000);
    }, []);

    // Close maintenance menu on outside click
    useEffect(() => {
        if (!showMaintenanceMenu) return;
        const handler = (e: MouseEvent) => {
            if (maintenanceRef.current && !maintenanceRef.current.contains(e.target as Node)) {
                setShowMaintenanceMenu(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [showMaintenanceMenu]);

    // Close on Escape key
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                if (convDetail) {
                    setConvDetail(null);
                } else if (showMaintenanceMenu) {
                    setShowMaintenanceMenu(false);
                } else if (showAddForm || editingId) {
                    setShowAddForm(false);
                    setEditingId(null);
                } else if (expandedRowId) {
                    setExpandedRowId(null);
                } else {
                    setShowMemoryDashboard(false);
                }
            }
        };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [convDetail, showMaintenanceMenu, showAddForm, editingId, expandedRowId, setShowMemoryDashboard]);

    // ── Data loading ────────────────────────────────────────────────────

    const loadStats = useCallback(async () => {
        try {
            const data = await memoryApi.getMemoryStats();
            setStats(data);
        } catch (err) {
            log.system.warn('Failed to load memory stats', err);
        }
    }, []);

    const loadActivity = useCallback(async () => {
        try {
            const data = await memoryApi.getMemoryActivity(30);
            setActivity(data);
        } catch (err) {
            log.system.warn('Failed to load memory activity', err);
        }
    }, []);

    const loadKnowledge = useCallback(async () => {
        try {
            const data = await memoryApi.getKnowledge({
                category: kCategory || undefined,
                context: kContext || undefined,
                entity: kEntity || undefined,
                search: kSearch || undefined,
                sort_by: kSort,
                order: kOrder,
                offset: kOffset,
                limit: kLimit,
                include_sensitive: true,
                include_superseded: includeSuperseded,
            });
            setKnowledge(data);
        } catch (err) {
            log.system.warn('Failed to load knowledge', err);
        }
    }, [kCategory, kContext, kEntity, kSearch, kSort, kOrder, kOffset, includeSuperseded]);

    const loadTools = useCallback(async () => {
        try {
            const data = await memoryApi.getToolSummary();
            setTools(data);
        } catch (err) {
            log.system.warn('Failed to load tool summary', err);
        }
    }, []);

    const loadConversations = useCallback(async () => {
        try {
            const data = await memoryApi.getMemoryConversations(20);
            setConversations(data);
        } catch (err) {
            log.system.warn('Failed to load conversations', err);
        }
    }, []);

    const loadUpcoming = useCallback(async () => {
        try {
            const data = await memoryApi.getUpcomingItems(7);
            setUpcoming(data);
        } catch (err) {
            log.system.warn('Failed to load upcoming items', err);
        }
    }, []);

    const loadEmbeddingCoverage = useCallback(async () => {
        try {
            const data = await memoryApi.getEmbeddingCoverage();
            setEmbeddingCoverage(data);
        } catch (err) {
            log.system.warn('Failed to load embedding coverage', err);
        }
    }, []);

    const loadEntities = useCallback(async () => {
        try {
            const data = await memoryApi.getEntities();
            setEntities(data || []);
        } catch (err) {
            log.system.warn('Failed to load entities', err);
        }
    }, []);

    const loadAll = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            await Promise.all([
                loadStats(),
                loadActivity(),
                loadKnowledge(),
                loadTools(),
                loadConversations(),
                loadUpcoming(),
                loadEmbeddingCoverage(),
                loadEntities(),
            ]);
        } catch (err) {
            setError('Failed to load memory data. Is the backend running?');
            log.system.error('Memory dashboard load failed', err);
        } finally {
            setLoading(false);
        }
    }, [loadStats, loadActivity, loadKnowledge, loadTools, loadConversations, loadUpcoming, loadEmbeddingCoverage, loadEntities]);

    useEffect(() => {
        loadAll();
    }, [loadAll]);

    // Reload knowledge when filters change
    useEffect(() => {
        loadKnowledge();
    }, [loadKnowledge]);

    // Debounced search
    const handleSearchChange = useCallback((value: string) => {
        setKSearch(value);
        setSearchPending(true);
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
            setKOffset(0);
            setSearchPending(false);
        }, 300);
    }, []);

    // ── Knowledge CRUD handlers ─────────────────────────────────────────

    const handleCreateKnowledge = useCallback(async () => {
        if (!formData.content.trim()) return;
        try {
            await memoryApi.createKnowledge({
                content: formData.content,
                category: formData.category,
                domain: formData.domain || undefined,
                context: formData.context,
                entity: formData.entity || undefined,
                sensitive: formData.sensitive,
                due_at: formData.due_at || undefined,
            });
            setShowAddForm(false);
            setFormData({ content: '', category: 'fact', domain: '', context: 'global', entity: '', sensitive: false, due_at: '' });
            loadKnowledge();
            loadStats();
            loadEmbeddingCoverage();
            log.ui.info('Created new knowledge entry');
            showToast('Memory created successfully', 'success');
        } catch (err) {
            log.system.error('Failed to create knowledge', err);
            showToast('Failed to create memory', 'error');
        }
    }, [formData, loadKnowledge, loadStats, loadEmbeddingCoverage, showToast]);

    const handleEditKnowledge = useCallback(async () => {
        if (!editingId || !formData.content.trim()) return;
        try {
            await memoryApi.editKnowledge(editingId, {
                content: formData.content,
                category: formData.category,
                domain: formData.domain || undefined,
                context: formData.context,
                entity: formData.entity || undefined,
                sensitive: formData.sensitive,
                due_at: formData.due_at || undefined,
            });
            setEditingId(null);
            setFormData({ content: '', category: 'fact', domain: '', context: 'global', entity: '', sensitive: false, due_at: '' });
            loadKnowledge();
            log.ui.info(`Updated knowledge ${editingId}`);
            showToast('Memory updated', 'success');
        } catch (err) {
            log.system.error('Failed to update knowledge', err);
            showToast('Failed to update memory', 'error');
        }
    }, [editingId, formData, loadKnowledge, showToast]);

    const handleDeleteKnowledge = useCallback(async (id: string) => {
        try {
            await memoryApi.deleteKnowledge(id);
            loadKnowledge();
            loadStats();
            loadEmbeddingCoverage();
            log.ui.info(`Deleted knowledge ${id}`);
            showToast('Memory deleted', 'info');
        } catch (err) {
            log.system.error('Failed to delete knowledge', err);
            showToast('Failed to delete memory', 'error');
        }
    }, [loadKnowledge, loadStats, loadEmbeddingCoverage, showToast]);

    const handleToggleSensitive = useCallback(async (entry: KnowledgeEntry) => {
        try {
            await memoryApi.editKnowledge(entry.id, { sensitive: !entry.sensitive });
            loadKnowledge();
            log.ui.info(`Toggled sensitive for ${entry.id}: ${!entry.sensitive}`);
            showToast(entry.sensitive ? 'Unmarked as sensitive' : 'Marked as sensitive', 'info');
        } catch (err) {
            log.system.error('Failed to toggle sensitive', err);
            showToast('Failed to toggle sensitive flag', 'error');
        }
    }, [loadKnowledge, showToast]);

    const startEdit = useCallback((entry: KnowledgeEntry) => {
        setEditingId(entry.id);
        setShowAddForm(false);
        setFormData({
            content: entry.content,
            category: entry.category,
            domain: entry.domain || '',
            context: entry.context,
            entity: entry.entity || '',
            sensitive: entry.sensitive,
            due_at: entry.due_at || '',
        });
    }, []);

    const copyId = useCallback((id: string) => {
        navigator.clipboard.writeText(id).catch(() => {});
        log.ui.info(`Copied knowledge ID: ${id}`);
        showToast('ID copied to clipboard', 'info');
    }, [showToast]);

    // ── Conversation search (server-side FTS) ───────────────────────────

    const handleConvSearchChange = useCallback((value: string) => {
        setConvSearch(value);
        if (convSearchTimeoutRef.current) clearTimeout(convSearchTimeoutRef.current);
        if (!value.trim()) {
            setConvSearchResults(null);
            return;
        }
        convSearchTimeoutRef.current = setTimeout(async () => {
            try {
                const results = await memoryApi.searchMemoryConversations(value.trim(), 20);
                setConvSearchResults(results as ConvTurn[]);
            } catch (err) {
                log.system.warn('Conversation search failed', err);
            }
        }, 300);
    }, []);

    // ── Conversation detail ─────────────────────────────────────────────

    const viewConversation = useCallback(async (sessionId: string) => {
        try {
            const turns = await memoryApi.getConversationDetail(sessionId);
            setConvDetail({ sessionId, turns });
        } catch (err) {
            log.system.error('Failed to load conversation detail', err);
        }
    }, []);

    // ── Sort handler ────────────────────────────────────────────────────

    const handleSort = useCallback((col: string) => {
        if (kSort === col) {
            setKOrder(prev => prev === 'desc' ? 'asc' : 'desc');
        } else {
            setKSort(col);
            setKOrder('desc');
        }
        setKOffset(0);
    }, [kSort]);

    // ── Maintenance handlers ────────────────────────────────────────────

    const handleConsolidate = useCallback(async () => {
        setConsolidating(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.consolidateSessions();
            await loadAll();
            showToast(
                `Consolidated ${result.consolidated ?? 0} sessions, extracted ${result.extracted_items ?? 0} items`,
                'success',
            );
        } catch (err) {
            log.system.error('Failed to consolidate sessions', err);
            showToast('Failed to consolidate sessions', 'error');
        } finally {
            setConsolidating(false);
        }
    }, [loadAll, showToast]);

    const handleRebuildEmbeddings = useCallback(async () => {
        setRebuildingEmbeddings(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.rebuildEmbeddings();
            await loadEmbeddingCoverage();
            showToast(
                `Backfilled ${result.backfilled ?? 0} embeddings${result.total_without ? ` (${result.total_without} were missing)` : ''}`,
                'success',
            );
        } catch (err) {
            log.system.error('Failed to rebuild embeddings', err);
            showToast('Failed to rebuild embeddings', 'error');
        } finally {
            setRebuildingEmbeddings(false);
        }
    }, [loadEmbeddingCoverage, showToast]);

    const handleReconcile = useCallback(async () => {
        setReconciling(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.reconcileMemory();
            await loadAll();
            showToast(
                `Checked ${result.pairs_checked ?? 0} pairs: ${result.reinforced ?? 0} reinforced, ${result.contradicted ?? 0} contradicted`,
                'success',
            );
        } catch (err) {
            log.system.error('Failed to reconcile memory', err);
            showToast('Failed to reconcile memory', 'error');
        } finally {
            setReconciling(false);
        }
    }, [loadAll, showToast]);

    const handleRebuildFts = useCallback(async () => {
        setRebuildingFts(true);
        setShowMaintenanceMenu(false);
        try {
            await memoryApi.rebuildFts();
            await loadAll();
            showToast('FTS indexes rebuilt successfully', 'success');
        } catch (err) {
            log.system.error('Failed to rebuild FTS index', err);
            showToast('Failed to rebuild FTS indexes', 'error');
        } finally {
            setRebuildingFts(false);
        }
    }, [loadAll, showToast]);

    const isMaintenanceRunning = consolidating || rebuildingEmbeddings || reconciling || rebuildingFts;

    // ── Render helpers ──────────────────────────────────────────────────

    const renderSortArrow = (col: string) => {
        if (kSort !== col) return null;
        return <span className="mem-sort-arrow">{kOrder === 'desc' ? '\u2193' : '\u2191'}</span>;
    };

    // Compute max value for activity chart scaling
    const activityMax = Math.max(1, ...activity.map(d =>
        d.conversations + d.tool_calls + d.knowledge_added + d.errors
    ));

    // Get unique contexts from stats for filter dropdowns
    const contexts = stats?.knowledge?.by_context ? Object.keys(stats.knowledge.by_context) : [];

    // ── Main render ─────────────────────────────────────────────────────

    return (
        <div className="memory-dashboard-overlay" onClick={() => setShowMemoryDashboard(false)}
             role="dialog" aria-modal="true" aria-label="Memory Dashboard">
            <div className="memory-dashboard-panel" onClick={e => e.stopPropagation()}>
                {/* Header */}
                <div className="memory-dashboard-header">
                    <h3>Memory Dashboard</h3>
                    <div className="memory-dashboard-header-actions">
                        {/* Embedding coverage indicator */}
                        {embeddingCoverage && embeddingCoverage.total_items > 0 && (
                            <div className="mem-embedding-coverage" title={`${embeddingCoverage.with_embedding} of ${embeddingCoverage.total_items} entries embedded`}>
                                <div className="mem-embedding-bar">
                                    <div
                                        className="mem-embedding-fill"
                                        style={{ width: `${Math.round(embeddingCoverage.coverage_pct)}%` }}
                                    />
                                </div>
                                <span className="mem-embedding-label">
                                    {Math.round(embeddingCoverage.coverage_pct)}% embedded
                                </span>
                            </div>
                        )}

                        {/* Maintenance dropdown */}
                        <div className="mem-maintenance-wrap" ref={maintenanceRef}>
                            <button
                                className={`btn-icon ${isMaintenanceRunning ? 'mem-spinning' : ''}`}
                                onClick={() => setShowMaintenanceMenu(prev => !prev)}
                                title="Maintenance actions"
                                aria-label="Maintenance actions"
                                aria-expanded={showMaintenanceMenu}
                            >
                                {isMaintenanceRunning ? (
                                    <div className="mem-spinner-sm" />
                                ) : (
                                    <Database size={16} />
                                )}
                                <ChevronDown size={10} style={{ marginLeft: 2 }} />
                            </button>
                            {showMaintenanceMenu && (
                                <div className="mem-maintenance-menu" role="menu">
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleConsolidate}
                                        disabled={consolidating}
                                        role="menuitem"
                                    >
                                        <GitMerge size={14} />
                                        {consolidating ? 'Consolidating...' : 'Consolidate Sessions'}
                                    </button>
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleRebuildEmbeddings}
                                        disabled={rebuildingEmbeddings}
                                        role="menuitem"
                                    >
                                        <Zap size={14} />
                                        {rebuildingEmbeddings ? 'Rebuilding...' : 'Rebuild Embeddings'}
                                    </button>
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleReconcile}
                                        disabled={reconciling}
                                        role="menuitem"
                                    >
                                        <CheckCircle size={14} />
                                        {reconciling ? 'Reconciling...' : 'Reconcile Memory'}
                                    </button>
                                    <div className="mem-maintenance-divider" />
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleRebuildFts}
                                        disabled={rebuildingFts}
                                        role="menuitem"
                                    >
                                        <Search size={14} />
                                        {rebuildingFts ? 'Rebuilding...' : 'Rebuild FTS Indexes'}
                                    </button>
                                </div>
                            )}
                        </div>

                        <button className="btn-icon" onClick={loadAll} title="Refresh" aria-label="Refresh dashboard">
                            <RefreshCw size={16} />
                        </button>
                        <button className="btn-icon" onClick={() => setShowMemoryDashboard(false)} aria-label="Close memory dashboard">
                            <X size={18} />
                        </button>
                    </div>
                </div>

                <div className="memory-dashboard-body">
                    {loading && !stats ? (
                        <div className="mem-loading">
                            <div className="mem-spinner" />
                            Loading memory data...
                        </div>
                    ) : error ? (
                        <div className="mem-empty">
                            <div className="mem-empty-icon"><AlertTriangle size={32} /></div>
                            <p>{error}</p>
                            <button className="btn-secondary" onClick={loadAll} style={{ marginTop: 12 }}>
                                <RefreshCw size={14} /> Retry
                            </button>
                        </div>
                    ) : (
                        <>
                            {/* ── 1. Header Stat Cards ─────────────── */}
                            <div className="mem-stat-cards">
                                <div className="mem-stat-card" data-accent="purple">
                                    <div className="mem-stat-value">{stats?.knowledge?.total ?? 0}</div>
                                    <div className="mem-stat-label">Memories</div>
                                    <div className="mem-stat-sub">
                                        {(() => {
                                            const addedToday = todayCount(activity);
                                            return addedToday > 0 ? `+${addedToday} today` : (
                                                stats?.knowledge?.by_category
                                                    ? Object.entries(stats.knowledge.by_category)
                                                        .slice(0, 3)
                                                        .map(([k, v]) => `${v} ${k}s`)
                                                        .join(', ')
                                                    : 'No entries'
                                            );
                                        })()}
                                    </div>
                                </div>
                                <div className="mem-stat-card" data-accent="blue">
                                    <div className="mem-stat-value">{stats?.conversations?.total_sessions ?? 0}</div>
                                    <div className="mem-stat-label">Sessions</div>
                                    <div className="mem-stat-sub">
                                        {formatDateRange(
                                            stats?.conversations?.first_session ?? null,
                                            stats?.conversations?.last_session ?? null,
                                        ) || `${stats?.conversations?.total_turns ?? 0} total turns`}
                                    </div>
                                </div>
                                <div className="mem-stat-card" data-accent="green">
                                    <div className="mem-stat-value">{stats?.tools?.total_calls ?? 0}</div>
                                    <div className="mem-stat-label">Tool Calls</div>
                                    <div className="mem-stat-sub">
                                        {stats?.tools?.unique_tools ?? 0} tools
                                    </div>
                                </div>
                                <div className="mem-stat-card" data-accent="red">
                                    <div className="mem-stat-value">
                                        {stats?.tools?.overall_success_rate != null
                                            ? `${Math.round(stats.tools.overall_success_rate * 100)}%`
                                            : '\u2014'}
                                    </div>
                                    <div className="mem-stat-label">Success Rate</div>
                                    <div className="mem-stat-sub">
                                        {stats?.tools?.total_errors ?? 0} errors
                                    </div>
                                </div>
                            </div>

                            {/* ── 2. Activity Timeline ─────────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title">
                                    <TrendingUp size={14} /> Activity (Last 30 Days)
                                </div>
                                {activity.length > 0 ? (
                                    <>
                                        <div className="mem-activity-chart">
                                            {activity.map((day, i) => {
                                                const total = day.conversations + day.tool_calls + day.knowledge_added + day.errors;
                                                const showLabel = i === 0 || i === activity.length - 1 || i % 7 === 0;
                                                const dateStr = new Date(day.date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                                                return (
                                                    <div key={day.date} className="mem-activity-bar-group"
                                                        onMouseEnter={() => setHoveredBar(i)}
                                                        onMouseLeave={() => setHoveredBar(null)}>
                                                        {day.errors > 0 && (
                                                            <div className="mem-activity-bar errors"
                                                                style={{ height: `${(day.errors / activityMax) * 100}%` }} />
                                                        )}
                                                        {day.knowledge_added > 0 && (
                                                            <div className="mem-activity-bar knowledge"
                                                                style={{ height: `${(day.knowledge_added / activityMax) * 100}%` }} />
                                                        )}
                                                        {day.tool_calls > 0 && (
                                                            <div className="mem-activity-bar tool_calls"
                                                                style={{ height: `${(day.tool_calls / activityMax) * 100}%` }} />
                                                        )}
                                                        {day.conversations > 0 && (
                                                            <div className="mem-activity-bar conversations"
                                                                style={{ height: `${(day.conversations / activityMax) * 100}%` }} />
                                                        )}
                                                        {showLabel && (
                                                            <span className="mem-activity-date-label">{dateStr}</span>
                                                        )}
                                                        {hoveredBar === i && total > 0 && (
                                                            <div className="mem-activity-tooltip">
                                                                <strong>{dateStr}</strong><br />
                                                                {day.conversations > 0 && <>{day.conversations} conversations<br /></>}
                                                                {day.tool_calls > 0 && <>{day.tool_calls} tool calls<br /></>}
                                                                {day.knowledge_added > 0 && <>{day.knowledge_added} learned<br /></>}
                                                                {day.errors > 0 && <>{day.errors} errors</>}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        <div className="mem-activity-legend">
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot conversations" /> Conversations
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot tool_calls" /> Tool Calls
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot knowledge" /> Knowledge
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot errors" /> Errors
                                            </span>
                                        </div>
                                    </>
                                ) : (
                                    <div className="mem-empty">
                                        <div className="mem-empty-icon"><TrendingUp size={28} /></div>
                                        <p>No activity data yet</p>
                                    </div>
                                )}
                            </div>

                            {/* ── 3. Knowledge Browser ─────────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title">
                                    <Brain size={14} /> Knowledge Browser
                                </div>

                                {/* Filters */}
                                <div className="mem-filters">
                                    <select className="mem-filter-select" value={kCategory}
                                        onChange={e => { setKCategory(e.target.value); setKOffset(0); }}
                                        aria-label="Filter by category">
                                        <option value="">All Categories</option>
                                        {ALL_CATEGORIES.map(c => (
                                            <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}s</option>
                                        ))}
                                    </select>
                                    <select className="mem-filter-select" value={kContext}
                                        onChange={e => { setKContext(e.target.value); setKOffset(0); }}
                                        aria-label="Filter by context">
                                        <option value="">All Contexts</option>
                                        {contexts.map(c => (
                                            <option key={c} value={c}>{c}</option>
                                        ))}
                                    </select>
                                    <select className="mem-filter-select" value={kEntity}
                                        onChange={e => { setKEntity(e.target.value); setKOffset(0); }}
                                        aria-label="Filter by entity">
                                        <option value="">All Entities</option>
                                        {entities.map(e => (
                                            <option key={e.entity} value={e.entity}>{e.entity} ({e.count})</option>
                                        ))}
                                    </select>
                                    <div className="mem-filter-search-wrap">
                                        {searchPending ? (
                                            <div className="mem-spinner-sm search-icon" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
                                        ) : (
                                            <Search size={13} className="search-icon" />
                                        )}
                                        <input className="mem-filter-search"
                                            type="text"
                                            placeholder="Search memories..."
                                            value={kSearch}
                                            onChange={e => handleSearchChange(e.target.value)}
                                            aria-label="Search memories"
                                        />
                                    </div>
                                    <label className="mem-filter-toggle" title="Show entries that have been superseded by newer versions">
                                        <input
                                            type="checkbox"
                                            checked={includeSuperseded}
                                            onChange={e => setIncludeSuperseded(e.target.checked)}
                                        />
                                        <span>Superseded</span>
                                    </label>
                                </div>

                                {/* Add / Edit form */}
                                {(showAddForm || editingId) && (
                                    <div className="mem-inline-form">
                                        <div className="mem-inline-form-row">
                                            <label>Content</label>
                                            <textarea value={formData.content}
                                                onChange={e => setFormData(prev => ({ ...prev, content: e.target.value }))}
                                                placeholder="What should the agent remember?" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Category</label>
                                            <select value={formData.category}
                                                onChange={e => setFormData(prev => ({ ...prev, category: e.target.value }))}>
                                                {ALL_CATEGORIES.map(c => (
                                                    <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Context</label>
                                            <input type="text" value={formData.context}
                                                onChange={e => setFormData(prev => ({ ...prev, context: e.target.value }))}
                                                placeholder="global" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Domain</label>
                                            <input type="text" value={formData.domain}
                                                onChange={e => setFormData(prev => ({ ...prev, domain: e.target.value }))}
                                                placeholder="e.g., python, frontend" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Entity</label>
                                            <input type="text" value={formData.entity}
                                                onChange={e => setFormData(prev => ({ ...prev, entity: e.target.value }))}
                                                placeholder="e.g., person:sarah_chen" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Due At</label>
                                            <input type="text" value={formData.due_at}
                                                onChange={e => setFormData(prev => ({ ...prev, due_at: e.target.value }))}
                                                placeholder="ISO 8601 (e.g., 2026-03-25T09:00:00-07:00)" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>Sensitive</label>
                                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12 }}>
                                                <input type="checkbox" checked={formData.sensitive}
                                                    onChange={e => setFormData(prev => ({ ...prev, sensitive: e.target.checked }))} />
                                                Mark as sensitive (excluded from system prompt)
                                            </label>
                                        </div>
                                        <div className="mem-inline-form-actions">
                                            <button className="btn-secondary"
                                                onClick={() => { setShowAddForm(false); setEditingId(null); }}>
                                                Cancel
                                            </button>
                                            <button className="btn-primary"
                                                onClick={editingId ? handleEditKnowledge : handleCreateKnowledge}
                                                style={{ padding: '8px 20px', fontSize: 13 }}>
                                                {editingId ? 'Update' : 'Save'}
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {/* Knowledge table */}
                                {knowledge && knowledge.items.length > 0 ? (
                                    <>
                                        <div style={{ overflowX: 'auto' }}>
                                            <table className="mem-knowledge-table">
                                                <thead>
                                                    <tr>
                                                        <th onClick={() => handleSort('category')} className={kSort === 'category' ? 'sorted' : ''}>
                                                            Category{renderSortArrow('category')}
                                                        </th>
                                                        <th>Content</th>
                                                        <th onClick={() => handleSort('context')} className={kSort === 'context' ? 'sorted' : ''}>
                                                            Context{renderSortArrow('context')}
                                                        </th>
                                                        <th>Entity</th>
                                                        <th onClick={() => handleSort('confidence')} className={kSort === 'confidence' ? 'sorted' : ''}>
                                                            Confidence{renderSortArrow('confidence')}
                                                        </th>
                                                        <th>Source</th>
                                                        <th>Due</th>
                                                        <th onClick={() => handleSort('updated_at')} className={kSort === 'updated_at' ? 'sorted' : ''}>
                                                            Updated{renderSortArrow('updated_at')}
                                                        </th>
                                                        <th></th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {knowledge.items.map(entry => {
                                                        const isExpanded = expandedRowId === entry.id;
                                                        const hasEmbed = entry.has_embedding;
                                                        return (
                                                        <React.Fragment key={entry.id}>
                                                        <tr
                                                            className={`${entry.superseded_by ? 'mem-superseded-row' : ''} ${isExpanded ? 'mem-expanded-row' : ''}`}
                                                            onClick={() => setExpandedRowId(isExpanded ? null : entry.id)}
                                                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpandedRowId(isExpanded ? null : entry.id); } }}
                                                            tabIndex={0}
                                                            role="row"
                                                            aria-expanded={isExpanded}
                                                            style={{ cursor: 'pointer' }}
                                                        >
                                                            <td>
                                                                <span className={`mem-cat-badge ${entry.category}`}>
                                                                    {entry.category}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                <div className="mem-content-cell" title={entry.content}>
                                                                    {/* Embedding status dot — only show when backend provides the field */}
                                                                    {hasEmbed !== undefined && (
                                                                        <span
                                                                            className={`mem-embed-dot ${hasEmbed ? 'embedded' : 'missing'}`}
                                                                            title={hasEmbed ? 'Embedded' : 'Embedding missing'}
                                                                        >
                                                                            <Circle size={6} fill="currentColor" />
                                                                        </span>
                                                                    )}
                                                                    {entry.sensitive && (
                                                                        <span className="mem-sensitive-badge" title="Sensitive">
                                                                            <Shield size={11} />
                                                                        </span>
                                                                    )}
                                                                    <span className={
                                                                        entry.sensitive && !revealedIds.has(entry.id)
                                                                            ? 'mem-sensitive-blur'
                                                                            : entry.superseded_by ? 'mem-superseded-text' : ''
                                                                    }
                                                                    onClick={(e) => {
                                                                        if (entry.sensitive) {
                                                                            e.stopPropagation();
                                                                            setRevealedIds(prev => {
                                                                                const next = new Set(prev);
                                                                                if (next.has(entry.id)) next.delete(entry.id);
                                                                                else next.add(entry.id);
                                                                                return next;
                                                                            });
                                                                        }
                                                                    }}>
                                                                        {entry.content}
                                                                    </span>
                                                                </div>
                                                            </td>
                                                            <td>
                                                                <span className="mem-date-cell">{entry.context}</span>
                                                            </td>
                                                            <td>
                                                                {entry.entity ? (
                                                                    <span className="mem-entity-link"
                                                                        onClick={(e) => { e.stopPropagation(); setKEntity(entry.entity!); setKOffset(0); }}
                                                                        title={`Filter by ${entry.entity}`}>
                                                                        {entry.entity}
                                                                    </span>
                                                                ) : (
                                                                    <span className="mem-date-cell">{'\u2014'}</span>
                                                                )}
                                                            </td>
                                                            <td>
                                                                <div className="mem-confidence">
                                                                    <div className="mem-confidence-bar">
                                                                        <div className={`mem-confidence-fill ${confidenceLevel(entry.confidence)}`}
                                                                            style={{ width: `${entry.confidence * 100}%` }} />
                                                                    </div>
                                                                    {entry.confidence.toFixed(2)}
                                                                </div>
                                                            </td>
                                                            <td>
                                                                <span className="mem-date-cell">{entry.source || '\u2014'}</span>
                                                            </td>
                                                            <td>
                                                                {entry.due_at ? (
                                                                    <span className={`mem-due-badge ${isOverdue(entry.due_at) ? 'overdue' : 'upcoming'}`}
                                                                        title={formatDateFull(entry.due_at)}>
                                                                        {formatRelativeDate(entry.due_at)}
                                                                    </span>
                                                                ) : (
                                                                    <span className="mem-due-badge none">{'\u2014'}</span>
                                                                )}
                                                            </td>
                                                            <td>
                                                                <span className="mem-date-cell" title={formatDateFull(entry.updated_at)}>
                                                                    {formatDate(entry.updated_at)}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                <div className="mem-row-actions" onClick={e => e.stopPropagation()}>
                                                                    <button className="mem-row-action-btn" onClick={() => startEdit(entry)} title="Edit">
                                                                        <Pencil size={13} />
                                                                    </button>
                                                                    <button className="mem-row-action-btn" onClick={() => handleToggleSensitive(entry)}
                                                                        title={entry.sensitive ? 'Unmark sensitive' : 'Mark sensitive'}>
                                                                        {entry.sensitive ? <ShieldOff size={13} /> : <Shield size={13} />}
                                                                    </button>
                                                                    <button className="mem-row-action-btn" onClick={() => copyId(entry.id)} title="Copy ID">
                                                                        <Copy size={13} />
                                                                    </button>
                                                                    <button className="mem-row-action-btn delete" onClick={() => handleDeleteKnowledge(entry.id)} title="Delete">
                                                                        <Trash2 size={13} />
                                                                    </button>
                                                                </div>
                                                            </td>
                                                        </tr>
                                                        {/* Expanded detail row */}
                                                        {isExpanded && (
                                                            <tr className="mem-detail-row">
                                                                <td colSpan={9}>
                                                                    <div className="mem-detail-panel">
                                                                        <div className="mem-detail-grid">
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">ID</span>
                                                                                <span className="mem-detail-value">{entry.id}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Domain</span>
                                                                                <span className="mem-detail-value">{entry.domain || '\u2014'}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Source</span>
                                                                                <span className="mem-detail-value">{entry.source || '\u2014'}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Use Count</span>
                                                                                <span className="mem-detail-value">{entry.use_count ?? 0}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Created</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.created_at)}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Updated</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.updated_at)}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Last Used</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.last_used)}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">Reminded At</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.reminded_at)}</span>
                                                                            </div>
                                                                            {entry.superseded_by && (
                                                                                <div className="mem-detail-field">
                                                                                    <span className="mem-detail-label">Superseded By</span>
                                                                                    <span className="mem-detail-value mem-detail-chain">{entry.superseded_by}</span>
                                                                                </div>
                                                                            )}
                                                                            {entry.metadata && (
                                                                                <div className="mem-detail-field mem-detail-full">
                                                                                    <span className="mem-detail-label">Metadata</span>
                                                                                    <span className="mem-detail-value">{entry.metadata}</span>
                                                                                </div>
                                                                            )}
                                                                        </div>
                                                                        <div className="mem-detail-content-full">
                                                                            <span className="mem-detail-label">Full Content</span>
                                                                            <div className="mem-detail-content-text">{entry.content}</div>
                                                                        </div>
                                                                    </div>
                                                                </td>
                                                            </tr>
                                                        )}
                                                        </React.Fragment>
                                                        );
                                                    })}
                                                </tbody>
                                            </table>
                                        </div>

                                        {/* Pagination */}
                                        <div className="mem-pagination">
                                            <span>
                                                {kOffset + 1}{'\u2013'}{Math.min(kOffset + kLimit, knowledge.total)} of {knowledge.total}
                                            </span>
                                            <div className="mem-pagination-btns">
                                                <button className="mem-page-btn"
                                                    disabled={kOffset === 0}
                                                    onClick={() => setKOffset(Math.max(0, kOffset - kLimit))}>
                                                    <ChevronLeft size={14} />
                                                </button>
                                                <button className="mem-page-btn"
                                                    disabled={kOffset + kLimit >= knowledge.total}
                                                    onClick={() => setKOffset(kOffset + kLimit)}>
                                                    <ChevronRight size={14} />
                                                </button>
                                            </div>
                                        </div>
                                    </>
                                ) : (
                                    <div className="mem-empty">
                                        <div className="mem-empty-icon"><Brain size={28} /></div>
                                        <p>{kSearch || kCategory || kContext || kEntity ? 'No matching memories' : 'No memories stored yet'}</p>
                                    </div>
                                )}

                                {!showAddForm && !editingId && (
                                    <button className="mem-add-btn" onClick={() => { setShowAddForm(true); setEditingId(null); }}>
                                        <Plus size={14} /> Add Memory
                                    </button>
                                )}
                            </div>

                            {/* ── Bottom two-column layout ─────────── */}
                            <div className="mem-two-col">
                                {/* ── 4. Tool Performance ─────────── */}
                                <div className="mem-section">
                                    <div className="mem-section-title">
                                        <Wrench size={14} /> Tool Performance
                                    </div>
                                    {tools.length > 0 ? (
                                        <div style={{ overflowX: 'auto' }}>
                                            <table className="mem-tool-table">
                                                <thead>
                                                    <tr>
                                                        <th>Tool</th>
                                                        <th>Calls</th>
                                                        <th>Success</th>
                                                        <th>Avg Time</th>
                                                        <th>Last Error</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {tools.map(t => (
                                                        <tr key={t.tool_name}>
                                                            <td><span className="mem-tool-name">{t.tool_name}</span></td>
                                                            <td>{t.total_calls}</td>
                                                            <td>
                                                                <div className="mem-confidence">
                                                                    <div className="mem-confidence-bar" style={{ width: 40 }}>
                                                                        <div className={`mem-confidence-fill ${successRateClass(t.success_rate)}`}
                                                                            style={{ width: `${t.success_rate * 100}%` }} />
                                                                    </div>
                                                                    <span className={`mem-success-rate ${successRateClass(t.success_rate)}`}>
                                                                        {Math.round(t.success_rate * 100)}%
                                                                    </span>
                                                                </div>
                                                            </td>
                                                            <td>{formatDuration(t.avg_duration_ms)}</td>
                                                            <td>
                                                                <span className="mem-tool-error" title={t.last_error || ''}>
                                                                    {t.last_error || '\u2014'}
                                                                </span>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <div className="mem-empty-icon"><Wrench size={28} /></div>
                                            <p>No tool calls recorded yet</p>
                                        </div>
                                    )}
                                </div>

                                {/* ── 6. Upcoming & Overdue ───────── */}
                                <div className="mem-section">
                                    <div className="mem-section-title">
                                        <Clock size={14} /> Upcoming & Overdue
                                    </div>
                                    {upcoming.length > 0 ? (
                                        <div className="mem-temporal-list">
                                            {upcoming.map(item => {
                                                const overdue = isOverdue(item.due_at);
                                                return (
                                                    <div key={item.id} className={`mem-temporal-item ${overdue ? 'overdue' : 'upcoming'}`}>
                                                        <span className="mem-temporal-icon">
                                                            {overdue ? <AlertTriangle size={16} /> : <Clock size={16} />}
                                                        </span>
                                                        <div className="mem-temporal-content">
                                                            <div className="mem-temporal-text">{item.content}</div>
                                                            <div className="mem-temporal-date">
                                                                {overdue ? 'OVERDUE' : 'Due'}: {formatRelativeDate(item.due_at)}
                                                                {item.context !== 'global' && ` \u00B7 ${item.context}`}
                                                            </div>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <div className="mem-empty-icon"><CheckCircle size={28} /></div>
                                            <p>Nothing upcoming or overdue</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* ── 5. Conversation History ─────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title">
                                    <MessageSquare size={14} /> Conversation History
                                </div>
                                <div className="mem-conv-search">
                                    <Search size={13} className="search-icon" />
                                    <input
                                        type="text"
                                        placeholder="Search conversations..."
                                        value={convSearch}
                                        onChange={e => handleConvSearchChange(e.target.value)}
                                        aria-label="Search conversations"
                                    />
                                </div>
                                {convSearchResults !== null ? (
                                    // Server-side FTS results: show matching turns
                                    convSearchResults.length > 0 ? (
                                        <div className="mem-conv-list">
                                            {convSearchResults.map(turn => (
                                                <div key={turn.id} className="mem-conv-item"
                                                    onClick={() => viewConversation(turn.session_id)}>
                                                    <div className="mem-conv-info">
                                                        <div className="mem-conv-session-id">
                                                            <span className={`mem-conv-turn-role ${turn.role}`}>{turn.role}</span>
                                                            {' '}{turn.session_id.slice(0, 8)}...
                                                        </div>
                                                        <div className="mem-conv-preview">{turn.content}</div>
                                                    </div>
                                                    <div className="mem-conv-meta">
                                                        <span>{formatDate(turn.timestamp)}</span>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <p>No conversations match &ldquo;{convSearch}&rdquo;</p>
                                        </div>
                                    )
                                ) : conversations.length > 0 ? (
                                    // Default: show recent sessions
                                    <div className="mem-conv-list">
                                        {conversations.map(c => (
                                            <div key={c.session_id} className="mem-conv-item"
                                                onClick={() => viewConversation(c.session_id)}>
                                                <div className="mem-conv-info">
                                                    <div className="mem-conv-session-id">
                                                        {c.session_id.slice(0, 8)}...
                                                        {c.consolidated && (
                                                            <span className="mem-consolidated-badge" title="Consolidated">
                                                                <GitMerge size={11} />
                                                            </span>
                                                        )}
                                                    </div>
                                                    {c.first_message && (
                                                        <div className="mem-conv-preview">{c.first_message}</div>
                                                    )}
                                                </div>
                                                <div className="mem-conv-meta">
                                                    <span>{c.turn_count} turns</span>
                                                    <span>{formatDate(c.last_activity)}</span>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="mem-empty">
                                        <div className="mem-empty-icon"><MessageSquare size={28} /></div>
                                        <p>No conversation history in memory</p>
                                    </div>
                                )}
                            </div>

                            {/* Conversation detail overlay */}
                            {convDetail && (
                                <div className="mem-conv-detail-overlay">
                                    <div className="mem-conv-detail-header">
                                        <h4>Session: {convDetail.sessionId.slice(0, 12)}...</h4>
                                        <button className="btn-icon" onClick={() => setConvDetail(null)}>
                                            <X size={16} />
                                        </button>
                                    </div>
                                    <div className="mem-conv-detail-body">
                                        {convDetail.turns.map(turn => (
                                            <div key={turn.id} className="mem-conv-turn">
                                                <div className={`mem-conv-turn-role ${turn.role}`}>
                                                    {turn.role}
                                                </div>
                                                <div className="mem-conv-turn-content">
                                                    {turn.content}
                                                </div>
                                            </div>
                                        ))}
                                        {convDetail.turns.length === 0 && (
                                            <div className="mem-empty">No turns found</div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* DB size footer */}
                            {stats && (
                                <div className="mem-db-footer">
                                    <span>
                                        Database: {formatBytes(stats.db_size_bytes)}
                                        {stats.knowledge?.avg_confidence != null &&
                                            ` \u00B7 Avg confidence: ${stats.knowledge.avg_confidence.toFixed(2)}`}
                                    </span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        {stats.knowledge?.entity_count != null && stats.knowledge.entity_count > 0 && (
                                            <span>{stats.knowledge.entity_count} entities</span>
                                        )}
                                        {stats.knowledge?.sensitive_count != null && stats.knowledge.sensitive_count > 0 && (
                                            <span><Shield size={10} /> {stats.knowledge.sensitive_count} sensitive</span>
                                        )}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>

            {/* Toast container */}
            {toasts.length > 0 && (
                <div className="mem-toast-container" aria-live="polite">
                    {toasts.map(t => (
                        <div key={t.id} className={`mem-toast mem-toast-${t.type}`} role="status">
                            {t.type === 'success' && <CheckCircle size={14} />}
                            {t.type === 'error' && <AlertTriangle size={14} />}
                            {t.type === 'info' && <Brain size={14} />}
                            <span>{t.text}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
