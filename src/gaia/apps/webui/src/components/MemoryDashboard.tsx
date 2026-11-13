// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import {
    X, Search, RefreshCw, Plus, Trash2, Pencil, Copy, Clock, ArrowLeft,
    Brain, MessageSquare, Wrench, TrendingUp, ChevronLeft, ChevronRight,
    AlertTriangle, CheckCircle, Shield, ShieldOff, ChevronDown, Database,
    Zap, GitMerge, Circle, User, Cpu, Sparkles, Target, ListChecks,
} from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useGoalHealthStore } from '../stores/goalHealthStore';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import * as memoryApi from '../services/memoryApi';
import { log } from '../utils/logger';
import { formatSize } from '../utils/format';
import { ref, type MessageKey } from '../i18n/registry';
import { dateLocale, useT, type Translate } from '../i18n/useT';
import { MemoryDashboardAboutYou } from './MemoryDashboardAboutYou';
import './MemoryDashboard.css';

// ── Type definitions ─────────────────────────────────────────────────────

interface MemoryStats {
    knowledge: {
        total: number;
        total_retrievals: number;
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
    /** Null when the coverage query failed; ``embedding_error`` says why. */
    embedding?: {
        total_items: number;
        with_embedding: number;
        without_embedding: number;
        coverage_pct: number;
    } | null;
    embedding_error?: string;
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
    metadata: Record<string, unknown> | string | null;
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
    denied_count?: number;
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

// imported from memoryApi but we alias for local use
type Goal = memoryApi.Goal;
type GoalTask = memoryApi.GoalTask;
type GoalStats = memoryApi.GoalStats;

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

function formatDate(iso: string | null, locale: string | undefined): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
        if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
        return d.toLocaleDateString(locale, opts);
    } catch { return '\u2014'; }
}

function formatDateFull(iso: string | null, locale: string | undefined): string {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleString(locale, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    } catch { return ''; }
}

function formatRelativeDate(iso: string | null, t: Translate): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        const now = Date.now();
        const diff = d.getTime() - now;
        const absDiff = Math.abs(diff);
        const secs = Math.floor(absDiff / 1000);
        const mins = Math.floor(absDiff / 60000);
        const hours = Math.floor(absDiff / 3600000);
        const days = Math.floor(absDiff / 86400000);

        if (diff < 0) {
            // Past
            if (secs < 5) return t('time.justNow');
            if (secs < 60) return t('mem.rel.secondsAgo', { n: secs });
            if (hours === 0) return t('time.minutesAgo', { n: mins });
            if (days === 0) return t('time.hoursAgo', { n: hours });
            if (days === 1) return t('time.yesterday');
            if (days < 30) return t('time.daysAgo', { n: days });
            return formatDateFull(iso, dateLocale(t));
        } else {
            // Future
            if (secs < 5) return t('mem.rel.now');
            if (secs < 60) return t('mem.rel.inSeconds', { n: secs });
            if (hours === 0) return t('mem.rel.inMinutes', { n: mins });
            if (days === 0) return t('mem.rel.inHours', { n: hours });
            if (days === 1) return t('mem.rel.tomorrow');
            if (days < 30) return t('mem.rel.inDays', { n: days });
            return formatDateFull(iso, dateLocale(t));
        }
    } catch { return '\u2014'; }
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

function formatDateRange(start: string | null, end: string | null, locale: string | undefined): string {
    const s = formatDate(start, locale);
    const e = formatDate(end, locale);
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

const CATEGORY_KEYS: Record<string, MessageKey> = {
    fact: 'mem.cat.fact', preference: 'mem.cat.preference', error: 'mem.cat.error',
    skill: 'mem.cat.skill', note: 'mem.cat.note', reminder: 'mem.cat.reminder',
};

const STATUS_KEYS: Record<string, MessageKey> = {
    pending_approval: 'mem.status.pending_approval', queued: 'mem.status.queued',
    in_progress: 'mem.status.in_progress', completed: 'mem.status.completed', failed: 'mem.status.failed',
    rejected: 'mem.status.rejected', cancelled: 'mem.status.cancelled', blocked: 'mem.status.blocked',
};

const PRIORITY_KEYS: Record<string, MessageKey> = { low: 'mem.priority.low', medium: 'mem.priority.medium', high: 'mem.priority.high' };

const GOAL_SOURCE_KEYS: Record<string, MessageKey> = {
    agent_inferred: 'mem.goals.source.agent_inferred', agent_scheduled: 'mem.goals.source.agent_scheduled',
};

const ROLE_KEYS: Record<string, MessageKey> = { user: 'mem.role.user', assistant: 'mem.role.assistant' };

const DOMAIN_KEYS: Record<string, MessageKey> = {
    work: 'mem.domain.work', technical: 'mem.domain.technical', personal: 'mem.domain.personal', general: 'mem.domain.general',
};

/** A server-side code in the user's language; one the UI has no words for stays as sent. */
function Code({ value, keys }: { value: string; keys: Record<string, MessageKey> }) {
    const t = useT();
    const key = keys[value];
    return key ? <>{t(key)}</> : <bdi>{value}</bdi>;
}

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

type ProfilePhase = 'idle' | 'running' | 'review' | 'saving' | 'done';

type DashboardTab = 'about' | 'goals' | 'profile' | 'advanced';

/** A profile-setup log line; `server` lines are the backend's own words, shown as sent. */
interface LogLine {
    text: string;
    kind: 'log' | 'finding' | 'error';
    server?: boolean;
}

interface CheckedDiscoveryFinding extends memoryApi.DiscoveryFinding {
    _checked: boolean;
}

interface CheckedInferenceInsight extends memoryApi.InferenceInsight {
    _checked: boolean;
}

const DOMAIN_COLORS: Record<string, string> = {
    work: 'blue',
    technical: 'purple',
    personal: 'green',
    general: 'gray',
};

// ── Main Component ──────────────────────────────────────────────────────

export function MemoryDashboard() {
    const t = useT();
    const { setShowMemoryDashboard, sessions, setCurrentSession } = useChatStore();
    const sessionTitles = useMemo(() => new Map(sessions.map(s => [s.id, s.title])), [sessions]);
    // Chats deleted from the sidebar keep their memory turns but have no title.
    const convLabel = (sessionId: string) => sessionTitles.get(sessionId) || `${sessionId.slice(0, 8)}...`;

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
    const [loadFailed, setLoadFailed] = useState(false);
    const [hoveredBar, setHoveredBar] = useState<number | null>(null);
    const [showMaintenanceMenu, setShowMaintenanceMenu] = useState(false);
    const [expandedRowId, setExpandedRowId] = useState<string | null>(null);
    const [toasts, setToasts] = useState<ToastMessage[]>([]);
    const toastIdRef = useRef(0);

    // Knowledge filters
    const [kCategories, setKCategories] = useState<string[]>([]);
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

    // Settings
    const [memoryEnabled, setMemoryEnabled] = useState(false);
    const [mcpMemoryEnabled, setMcpMemoryEnabled] = useState(false);
    const [systemDiscoveryConsent, setSystemDiscoveryConsent] = useState(false);
    const [settingsLoading, setSettingsLoading] = useState(false);
    const [deleteConfirm, setDeleteConfirm] = useState(false);
    const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
    const [reinitConfirm, setReinitConfirm] = useState(false);
    const [betaConfirm, setBetaConfirm] = useState<null | 'memory' | 'mcp' | 'discovery'>(null);

    // Tab state
    const [activeTab, setActiveTab] = useState<DashboardTab>('about');
    const [aboutReloadKey, setAboutReloadKey] = useState(0);

    // Goals tab state
    const [goals, setGoals] = useState<Goal[]>([]);
    const [goalStats, setGoalStats] = useState<GoalStats | null>(null);
    const [goalsLoading, setGoalsLoading] = useState(false);
    const goalStoreError = useGoalHealthStore((s) => s.error);
    const [expandedGoalId, setExpandedGoalId] = useState<string | null>(null);
    const [confirmGoalDeleteId, setConfirmGoalDeleteId] = useState<string | null>(null);
    const [showAddGoalForm, setShowAddGoalForm] = useState(false);
    const [goalForm, setGoalForm] = useState({ title: '', description: '', priority: 'medium' as memoryApi.Priority });

    // Profile Setup — Discovery
    const [discPhase, setDiscPhase] = useState<ProfilePhase>('idle');
    const [discLogs, setDiscLogs] = useState<LogLine[]>([]);
    const [discItems, setDiscItems] = useState<CheckedDiscoveryFinding[]>([]);
    const [discSavedCount, setDiscSavedCount] = useState<number | null>(null);
    const discLogRef = useRef<HTMLDivElement>(null);
    const discEsRef = useRef<EventSource | null>(null);

    // Profile Setup — Inference
    const [infPhase, setInfPhase] = useState<ProfilePhase>('idle');
    const [infLogs, setInfLogs] = useState<LogLine[]>([]);
    const [infItems, setInfItems] = useState<CheckedInferenceInsight[]>([]);
    const [infSavedCount, setInfSavedCount] = useState<number | null>(null);
    const [infIncludeBrowser, setInfIncludeBrowser] = useState(false);
    const infLogRef = useRef<HTMLDivElement>(null);
    const infEsRef = useRef<EventSource | null>(null);

    // Activity range
    const [activityDays, setActivityDays] = useState(30);

    // Maintenance state
    const [consolidating, setConsolidating] = useState(false);
    const [rebuildingEmbeddings, setRebuildingEmbeddings] = useState(false);
    const [reconciling, setReconciling] = useState(false);
    const [rebuildingFts, setRebuildingFts] = useState(false);
    const [refreshingSystem, setRefreshingSystem] = useState(false);
    const [searchPending, setSearchPending] = useState(false);

    const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const maintenanceRef = useRef<HTMLDivElement>(null);
    const betaConfirmRef = useRef<HTMLDivElement>(null);

    // ── Toast helpers ──────────────────────────────────────────────────

    const showToast = useCallback((text: string, type: ToastMessage['type'] = 'info') => {
        const id = ++toastIdRef.current;
        setToasts(prev => [...prev, { id, text, type }]);
        setTimeout(() => {
            setToasts(prev => prev.filter(toast => toast.id !== id));
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

    // Escape backs out one level: the innermost open piece first, then the dashboard.
    useEscapeToClose(true, () => {
        if (betaConfirm) {
            setBetaConfirm(null);
        } else if (deleteConfirm) {
            setDeleteConfirm(false);
        } else if (reinitConfirm) {
            setReinitConfirm(false);
        } else if (convDetail) {
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
    });

    // Modal focus handling for the beta-confirm dialog: move focus in, keep Tab
    // inside it, and hand focus back to the toggle that opened it.
    useFocusTrap(betaConfirmRef, !!betaConfirm);
    useEffect(() => {
        if (!betaConfirm) return;
        const panel = betaConfirmRef.current;
        if (!panel) return;
        const opener = document.activeElement as HTMLElement | null;
        panel.focus();
        return () => {
            opener?.focus();
        };
    }, [betaConfirm]);

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
            const data = await memoryApi.getMemoryActivity(activityDays);
            setActivity(data);
        } catch (err) {
            log.system.warn('Failed to load memory activity', err);
        }
    }, [activityDays]);

    const loadKnowledge = useCallback(async () => {
        try {
            const data = await memoryApi.getKnowledge({
                category: kCategories.length > 0 ? kCategories : undefined,
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
    }, [kCategories, kContext, kEntity, kSearch, kSort, kOrder, kOffset, includeSuperseded]);

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

    const loadSettings = useCallback(async () => {
        try {
            const s = await memoryApi.getMemorySettings();
            setMcpMemoryEnabled(s.mcp_memory_enabled);
            setMemoryEnabled(s.memory_enabled);
            setSystemDiscoveryConsent(s.system_discovery_consent);
        } catch (err) {
            log.system.warn('Failed to load memory settings', err);
        }
    }, []);

    const loadGoals = useCallback(async () => {
        setGoalsLoading(true);
        void useGoalHealthStore.getState().check();
        try {
            const [goalsRes, statsRes] = await Promise.all([
                memoryApi.listGoals(),
                memoryApi.getGoalStats(),
            ]);
            setGoals(goalsRes.goals);
            setGoalStats(statsRes);
        } catch (err) {
            log.system.warn('Failed to load goals', err);
        } finally {
            setGoalsLoading(false);
        }
    }, []);

    /** Run a goal action, reload, and say so when it fails instead of leaving the form as-is. */
    const runGoalAction = useCallback(async (action: () => Promise<unknown>, failure: MessageKey) => {
        try {
            await action();
        } catch (err) {
            log.system.error(`Goal action failed (${failure})`, err);
            showToast(t('mem.goals.failed', { what: ref(failure), error: errorText(err) }), 'error');
            return;
        }
        loadGoals();
    }, [loadGoals, showToast, t]);

    useEffect(() => {
        if (!confirmGoalDeleteId) return;
        const t = setTimeout(() => setConfirmGoalDeleteId(null), 3000);
        return () => clearTimeout(t);
    }, [confirmGoalDeleteId]);

    const loadAll = useCallback(async () => {
        setLoading(true);
        setLoadFailed(false);
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
                loadSettings(),
                loadGoals(),
            ]);
            setAboutReloadKey(k => k + 1);
        } catch (err) {
            setLoadFailed(true);
            log.system.error('Memory dashboard load failed', err);
        } finally {
            setLoading(false);
        }
    }, [loadStats, loadActivity, loadKnowledge, loadTools, loadConversations, loadUpcoming, loadEmbeddingCoverage, loadEntities, loadSettings, loadGoals]);

    useEffect(() => {
        loadAll();
    }, [loadAll]);

    // Reload knowledge when filters change
    useEffect(() => {
        loadKnowledge();
    }, [loadKnowledge]);

    // Reload activity when range changes (skip on initial mount — loadAll covers it)
    const isFirstRender = useRef(true);
    useEffect(() => {
        if (isFirstRender.current) { isFirstRender.current = false; return; }
        loadActivity();
    }, [loadActivity]);

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

    // Every panel that shows a memory: a changed due date or entity moves it
    // in Upcoming and the entity filter, not just in the table.
    const refreshAdvancedViews = useCallback(() => {
        loadKnowledge();
        loadStats();
        loadEmbeddingCoverage();
        loadUpcoming();
        loadEntities();
    }, [loadKnowledge, loadStats, loadEmbeddingCoverage, loadUpcoming, loadEntities]);

    const refreshKnowledgeViews = useCallback(() => {
        refreshAdvancedViews();
        setAboutReloadKey(k => k + 1);
    }, [refreshAdvancedViews]);

    const handleCreateKnowledge = useCallback(async () => {
        if (!formData.content.trim()) return;
        try {
            const created = await memoryApi.createKnowledge({
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
            refreshKnowledgeViews();
            log.ui.info('Created new knowledge entry');
            if (created?.embedded === false) showToast(created.embed_error ? t('mem.toast.server', { message: created.embed_error }) : t('mem.toast.notEmbedded'), 'error');
            else showToast(t('mem.toast.created'), 'success');
        } catch (err) {
            log.system.error('Failed to create knowledge', err);
            showToast(t('mem.toast.createFailed', { error: errorText(err) }), 'error');
        }
    }, [formData, refreshKnowledgeViews, showToast, t]);

    const handleEditKnowledge = useCallback(async () => {
        if (!editingId || !formData.content.trim()) return;
        try {
            const updated = await memoryApi.editKnowledge(editingId, {
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
            refreshKnowledgeViews();
            log.ui.info(`Updated knowledge ${editingId}`);
            if (updated?.embedded === false) showToast(updated.embed_error ? t('mem.toast.server', { message: updated.embed_error }) : t('mem.toast.notEmbedded'), 'error');
            else showToast(t('mem.toast.updated'), 'success');
        } catch (err) {
            log.system.error('Failed to update knowledge', err);
            showToast(t('mem.toast.updateFailed', { error: errorText(err) }), 'error');
        }
    }, [editingId, formData, refreshKnowledgeViews, showToast, t]);

    const handleDeleteKnowledge = useCallback(async (id: string) => {
        if (confirmDeleteId !== id) {
            setConfirmDeleteId(id);
            return;
        }
        setConfirmDeleteId(null);
        try {
            await memoryApi.deleteKnowledge(id);
            refreshKnowledgeViews();
            log.ui.info(`Deleted knowledge ${id}`);
            showToast(t('mem.toast.deleted'), 'info');
        } catch (err) {
            log.system.error('Failed to delete knowledge', err);
            showToast(t('mem.toast.deleteFailed', { error: errorText(err) }), 'error');
        }
    }, [confirmDeleteId, refreshKnowledgeViews, showToast, t]);

    useEffect(() => {
        if (!confirmDeleteId) return;
        const t = setTimeout(() => setConfirmDeleteId(null), 3000);
        return () => clearTimeout(t);
    }, [confirmDeleteId]);

    const handleToggleSensitive = useCallback(async (entry: KnowledgeEntry) => {
        try {
            await memoryApi.editKnowledge(entry.id, { sensitive: !entry.sensitive });
            loadKnowledge();
            log.ui.info(`Toggled sensitive for ${entry.id}: ${!entry.sensitive}`);
            showToast(t(entry.sensitive ? 'mem.toast.unmarkedSensitive' : 'mem.toast.markedSensitive'), 'info');
        } catch (err) {
            log.system.error('Failed to toggle sensitive', err);
            showToast(t('mem.toast.sensitiveFailed', { error: errorText(err) }), 'error');
        }
    }, [loadKnowledge, showToast, t]);

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
        navigator.clipboard.writeText(id).then(
            () => showToast(t('mem.toast.idCopied'), 'info'),
            (err) => {
                log.ui.warn(`Couldn't copy knowledge ID ${id}`, err);
                showToast(t('mem.toast.copyFailed', { error: errorText(err) }), 'error');
            },
        );
    }, [showToast, t]);

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

    // ── Category multi-select ───────────────────────────────────────────

    const toggleCategory = useCallback((cat: string) => {
        setKCategories(prev =>
            prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
        );
        setKOffset(0);
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
                t('mem.toast.consolidated', { sessions: result.consolidated ?? 0, items: result.extracted_items ?? 0 }),
                'success',
            );
        } catch (err) {
            log.system.error('Failed to consolidate sessions', err);
            showToast(t('mem.toast.consolidateFailed', { error: errorText(err) }), 'error');
        } finally {
            setConsolidating(false);
        }
    }, [loadAll, showToast, t]);

    const handleRebuildEmbeddings = useCallback(async () => {
        setRebuildingEmbeddings(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.rebuildEmbeddings();
            await loadEmbeddingCoverage();
            showToast(
                result.total_without
                    ? t('mem.toast.backfilledMissing', { n: result.backfilled ?? 0, missing: result.total_without })
                    : t('mem.toast.backfilled', { n: result.backfilled ?? 0 }),
                'success',
            );
        } catch (err) {
            log.system.error('Failed to rebuild embeddings', err);
            showToast(t('mem.toast.rebuildFailed', { error: errorText(err) }), 'error');
        } finally {
            setRebuildingEmbeddings(false);
        }
    }, [loadEmbeddingCoverage, showToast, t]);

    const handleReconcile = useCallback(async () => {
        setReconciling(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.reconcileMemory();
            await loadAll();
            showToast(
                t('mem.toast.reconciled', {
                    pairs: result.pairs_checked ?? 0, reinforced: result.reinforced ?? 0, contradicted: result.contradicted ?? 0,
                }),
                'success',
            );
        } catch (err) {
            log.system.error('Failed to reconcile memory', err);
            showToast(t('mem.toast.reconcileFailed', { error: errorText(err) }), 'error');
        } finally {
            setReconciling(false);
        }
    }, [loadAll, showToast, t]);

    const handleRebuildFts = useCallback(async () => {
        setRebuildingFts(true);
        setShowMaintenanceMenu(false);
        try {
            await memoryApi.rebuildFts();
            await loadAll();
            showToast(t('mem.toast.ftsRebuilt'), 'success');
        } catch (err) {
            log.system.error('Failed to rebuild FTS index', err);
            showToast(t('mem.toast.ftsFailed', { error: errorText(err) }), 'error');
        } finally {
            setRebuildingFts(false);
        }
    }, [loadAll, showToast, t]);

    const handleRefreshSystemContext = useCallback(async () => {
        setRefreshingSystem(true);
        setShowMaintenanceMenu(false);
        try {
            const result = await memoryApi.refreshSystemContext();
            await loadAll();
            if (result.skipped) {
                showToast(t('mem.toast.systemDisabled'), 'error');
            } else {
                showToast(t('mem.toast.systemRefreshed', { n: result.stored }), 'success');
            }
        } catch (err) {
            log.system.error('Failed to refresh system context', err);
            showToast(t('mem.toast.systemFailed', { error: errorText(err) }), 'error');
        } finally {
            setRefreshingSystem(false);
        }
    }, [loadAll, showToast, t]);

    const isMaintenanceRunning = consolidating || rebuildingEmbeddings || reconciling || rebuildingFts || refreshingSystem;

    // ── Profile Setup: Discovery ───────────────────────────────────────

    const runDiscovery = useCallback(() => {
        setDiscPhase('running');
        setDiscLogs([]);
        setDiscItems([]);
        setDiscSavedCount(null);

        const es = memoryApi.openDiscoveryStream();
        discEsRef.current = es;

        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'log') {
                    setDiscLogs(prev => [...prev, { text: data.message, kind: 'log', server: true }]);
                } else if (data.type === 'finding') {
                    const item = data.item as memoryApi.DiscoveryFinding;
                    // Bare: a bracketed English copy would repeat the whole item on every log line.
                    setDiscLogs(prev => [...prev, { text: t.bare('mem.log.found', { content: item.content }), kind: 'finding' }]);
                    setDiscItems(prev => [...prev, { ...item, _checked: true }]);
                } else if (data.type === 'error') {
                    setDiscLogs(prev => [...prev, { text: t.bare('mem.log.error', { message: data.message }), kind: 'error' }]);
                } else if (data.type === 'done') {
                    es.close();
                    discEsRef.current = null;
                    setDiscLogs(prev => [...prev, { text: t('mem.log.discoveryDone', { n: data.total ?? 0 }), kind: 'log' }]);
                    setDiscPhase('review');
                }
            } catch (err) {
                log.system.warn('Unreadable discovery event', err);
                setDiscLogs(prev => [...prev, { text: t('mem.log.parseFailed'), kind: 'error' }]);
            }
        };

        es.onerror = () => {
            es.close();
            discEsRef.current = null;
            setDiscLogs(prev => [...prev, { text: t('mem.log.discoveryConnection'), kind: 'error' }]);
            // Only go to review if items were found; otherwise stay idle so user can retry
            setDiscItems(prev => {
                setDiscPhase(prev.length > 0 ? 'review' : 'idle');
                return prev;
            });
        };
    }, [t]);

    const saveDiscoveryItems = useCallback(async () => {
        const selected = discItems.filter(i => i._checked);
        if (selected.length === 0) return;
        setDiscPhase('saving');
        try {
            // Strip the local _checked field before sending
            const payload = selected.map(({ _checked, ...rest }) => rest);
            const result = await memoryApi.commitDiscovery(payload);
            setDiscSavedCount(result.stored);
            setDiscPhase('done');
            refreshKnowledgeViews();
            showToast(t('mem.disc.saved', { n: result.stored }), 'success');
        } catch (err) {
            log.system.error('Failed to commit discovery items', err);
            showToast(t('mem.disc.saveFailed', { error: errorText(err) }), 'error');
            setDiscPhase('review');
        }
    }, [discItems, refreshKnowledgeViews, showToast, t]);

    // ── Profile Setup: Inference ───────────────────────────────────────

    const runInference = useCallback(() => {
        setInfPhase('running');
        setInfLogs([]);
        setInfItems([]);
        setInfSavedCount(null);

        const es = memoryApi.openInferenceStream(infIncludeBrowser);
        infEsRef.current = es;

        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'log') {
                    setInfLogs(prev => [...prev, { text: data.message, kind: 'log', server: true }]);
                } else if (data.type === 'insight') {
                    const item = data.item as memoryApi.InferenceInsight;
                    setInfLogs(prev => [...prev, { text: t.bare('mem.log.insight', { content: item.content }), kind: 'finding' }]);
                    setInfItems(prev => [...prev, { ...item, _checked: true }]);
                } else if (data.type === 'error') {
                    setInfLogs(prev => [...prev, { text: t.bare('mem.log.error', { message: data.message }), kind: 'error' }]);
                } else if (data.type === 'done') {
                    es.close();
                    infEsRef.current = null;
                    setInfLogs(prev => [...prev, { text: t('mem.log.inferenceDone', { n: data.total ?? 0 }), kind: 'log' }]);
                    setInfPhase('review');
                }
            } catch (err) {
                log.system.warn('Unreadable inference event', err);
                setInfLogs(prev => [...prev, { text: t('mem.log.parseFailed'), kind: 'error' }]);
            }
        };

        es.onerror = () => {
            es.close();
            infEsRef.current = null;
            setInfLogs(prev => [...prev, { text: t('mem.log.inferenceConnection'), kind: 'error' }]);
            setInfItems(prev => {
                setInfPhase(prev.length > 0 ? 'review' : 'idle');
                return prev;
            });
        };
    }, [infIncludeBrowser, t]);

    const saveInferenceItems = useCallback(async () => {
        const selected = infItems.filter(i => i._checked);
        if (selected.length === 0) return;
        setInfPhase('saving');
        try {
            const payload = selected.map(({ _checked, ...rest }) => rest);
            const result = await memoryApi.commitInference(payload);
            setInfSavedCount(result.stored);
            setInfPhase('done');
            refreshKnowledgeViews();
            showToast(t('mem.inf.saved', { n: result.stored }), 'success');
        } catch (err) {
            log.system.error('Failed to commit inference insights', err);
            showToast(t('mem.inf.saveFailed', { error: errorText(err) }), 'error');
            setInfPhase('review');
        }
    }, [infItems, refreshKnowledgeViews, showToast, t]);

    // Auto-scroll log containers
    useEffect(() => {
        if (discLogRef.current) {
            discLogRef.current.scrollTop = discLogRef.current.scrollHeight;
        }
    }, [discLogs]);

    useEffect(() => {
        if (infLogRef.current) {
            infLogRef.current.scrollTop = infLogRef.current.scrollHeight;
        }
    }, [infLogs]);

    // Cleanup EventSources on unmount
    useEffect(() => {
        return () => {
            if (discEsRef.current) { discEsRef.current.close(); discEsRef.current = null; }
            if (infEsRef.current) { infEsRef.current.close(); infEsRef.current = null; }
        };
    }, []);

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
        <div className="memory-dashboard-page">
                {/* Header */}
                <div className="memory-dashboard-header">
                    <div className="memory-dashboard-header-left">
                        <button
                            className="btn-icon settings-back-btn"
                            onClick={() => setShowMemoryDashboard(false)}
                            aria-label={t('mem.back')}
                        >
                            <ArrowLeft size={18} />
                        </button>
                        <h1>{t('mem.title')} <span className="mem-beta" style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-yellow)', background: 'var(--accent-yellow-dim)', padding: '2px 8px', borderRadius: 4, marginInlineStart: 8, verticalAlign: 'middle', whiteSpace: 'nowrap' }}>{t('mem.beta')}</span></h1>
                    </div>
                    <div className="memory-dashboard-header-actions">
                        {activeTab === 'advanced' && <>
                        {/* Embedding coverage indicator */}
                        {embeddingCoverage && embeddingCoverage.total_items > 0 && (
                            <div className="mem-embedding-coverage" title={t('mem.coverage.title', { n: embeddingCoverage.with_embedding, total: embeddingCoverage.total_items })}>
                                <div className="mem-embedding-bar">
                                    <div
                                        className="mem-embedding-fill"
                                        style={{ width: `${Math.round(embeddingCoverage.coverage_pct)}%` }}
                                    />
                                </div>
                                <span className="mem-embedding-label">
                                    {t('mem.coverage.label', { pct: `${Math.round(embeddingCoverage.coverage_pct)}%` })}
                                </span>
                            </div>
                        )}

                        {/* Maintenance dropdown */}
                        <div className="mem-maintenance-wrap" ref={maintenanceRef}>
                            <button
                                className={`btn-icon ${isMaintenanceRunning ? 'mem-spinning' : ''}`}
                                onClick={() => setShowMaintenanceMenu(prev => !prev)}
                                title={t('mem.maint.label')}
                                aria-label={t('mem.maint.label')}
                                aria-expanded={showMaintenanceMenu}
                            >
                                {isMaintenanceRunning ? (
                                    <div className="mem-spinner-sm" />
                                ) : (
                                    <Database size={16} />
                                )}
                                <ChevronDown size={10} style={{ marginInlineStart: 2 }} />
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
                                        {t(consolidating ? 'mem.maint.consolidating' : 'mem.maint.consolidate')}
                                    </button>
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleRebuildEmbeddings}
                                        disabled={rebuildingEmbeddings}
                                        role="menuitem"
                                    >
                                        <Zap size={14} />
                                        {t(rebuildingEmbeddings ? 'mem.maint.rebuilding' : 'mem.maint.rebuildEmbeddings')}
                                    </button>
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleReconcile}
                                        disabled={reconciling}
                                        role="menuitem"
                                    >
                                        <CheckCircle size={14} />
                                        {t(reconciling ? 'mem.maint.reconciling' : 'mem.maint.reconcile')}
                                    </button>
                                    <div className="mem-maintenance-divider" />
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleRefreshSystemContext}
                                        disabled={refreshingSystem}
                                        role="menuitem"
                                    >
                                        <RefreshCw size={14} />
                                        {t(refreshingSystem ? 'mem.maint.refreshing' : 'mem.maint.refreshSystem')}
                                    </button>
                                    <div className="mem-maintenance-divider" />
                                    <button
                                        className="mem-maintenance-item"
                                        onClick={handleRebuildFts}
                                        disabled={rebuildingFts}
                                        role="menuitem"
                                    >
                                        <Search size={14} />
                                        {t(rebuildingFts ? 'mem.maint.rebuilding' : 'mem.maint.rebuildFts')}
                                    </button>
                                </div>
                            )}
                        </div>
                        </>}

                        <button className="btn-icon" onClick={loadAll} title={t('mem.refresh')} aria-label={t('mem.refreshAria')}>
                            <RefreshCw size={16} />
                        </button>
                    </div>
                </div>

                {/* Tab bar */}
                <div className="mem-tab-bar" role="tablist">
                    <button
                        className={`mem-tab-btn${activeTab === 'about' ? ' mem-tab-active' : ''}`}
                        onClick={() => setActiveTab('about')}
                        role="tab"
                        aria-selected={activeTab === 'about'}
                    >
                        <User size={14} /> {t('mem.tab.about')}
                    </button>
                    <button
                        className={`mem-tab-btn${activeTab === 'goals' ? ' mem-tab-active' : ''}`}
                        onClick={() => { setActiveTab('goals'); loadGoals(); }}
                        role="tab"
                        aria-selected={activeTab === 'goals'}
                    >
                        <Target size={14} /> {t('mem.tab.goals')}
                        {goalStats && (goalStats.goals['pending_approval'] ?? 0) > 0 && (
                            <span className="mem-tab-badge">{goalStats.goals['pending_approval']}</span>
                        )}
                    </button>
                    <button
                        className={`mem-tab-btn${activeTab === 'profile' ? ' mem-tab-active' : ''}`}
                        onClick={() => setActiveTab('profile')}
                        role="tab"
                        aria-selected={activeTab === 'profile'}
                    >
                        <Sparkles size={14} /> {t('mem.tab.profile')}
                    </button>
                    <button
                        className={`mem-tab-btn${activeTab === 'advanced' ? ' mem-tab-active' : ''}`}
                        onClick={() => setActiveTab('advanced')}
                        role="tab"
                        aria-selected={activeTab === 'advanced'}
                    >
                        <Brain size={14} /> {t('mem.tab.advanced')}
                    </button>
                </div>

                <div className="memory-dashboard-body">
                    {activeTab === 'about' && (
                        <MemoryDashboardAboutYou
                            memoryEnabled={memoryEnabled}
                            reloadKey={aboutReloadKey}
                            onChanged={refreshAdvancedViews}
                            onOpenAdvanced={() => setActiveTab('advanced')}
                            showToast={showToast}
                        />
                    )}

                    {activeTab === 'advanced' && (<>
                    {loading && !stats ? (
                        <div className="mem-loading">
                            <div className="mem-spinner" />
                            {t('mem.loadingData')}
                        </div>
                    ) : loadFailed ? (
                        <div className="mem-empty">
                            <div className="mem-empty-icon"><AlertTriangle size={32} /></div>
                            <p>{t('mem.loadFailed')}</p>
                            <button className="btn-secondary" onClick={loadAll} style={{ marginTop: 12 }}>
                                <RefreshCw size={14} /> {t('mem.retry')}
                            </button>
                        </div>
                    ) : (
                        <>
                            {!memoryEnabled && (
                                <div className="mem-empty-state" style={{ margin: '40px 0' }}>
                                    <div className="mem-empty-icon" style={{ opacity: 0.5 }}>
                                        <Shield size={32} />
                                    </div>
                                    <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>{t('mem.offAdvanced')}</p>
                                </div>
                            )}

                            {memoryEnabled && <>
                            {stats?.embedding_error && (
                                <div className="mem-advanced-error" role="alert">
                                    <AlertTriangle size={14} /> <bdi>{stats.embedding_error}</bdi>
                                </div>
                            )}
                            {/* ── 1. Header Stat Cards ─────────────── */}
                            <div className="mem-stat-cards">
                                <div className="mem-stat-card">
                                    <div className="mem-stat-value">{stats?.knowledge?.total ?? 0}</div>
                                    <div className="mem-stat-label">{t('mem.stat.stored')}</div>
                                    <div className="mem-stat-sub">
                                        {(() => {
                                            const addedToday = todayCount(activity);
                                            return addedToday > 0 ? t('mem.stat.addedToday', { n: addedToday }) : (
                                                stats?.knowledge?.by_category
                                                    ? t.compose(say => Object.entries(stats.knowledge.by_category)
                                                        .slice(0, 3)
                                                        .map(([k, v]) => say('mem.stat.categoryCount', {
                                                            category: CATEGORY_KEYS[k] ? ref(CATEGORY_KEYS[k]) : k, n: v,
                                                        }))
                                                        .join(say('mem.listSeparator')))
                                                    : t('mem.stat.noEntries')
                                            );
                                        })()}
                                    </div>
                                </div>
                                <div className="mem-stat-card">
                                    <div className="mem-stat-value">{stats?.knowledge?.total_retrievals ?? 0}</div>
                                    <div className="mem-stat-label">{t('mem.stat.retrieved')}</div>
                                    <div className="mem-stat-sub">
                                        {stats?.knowledge?.total && stats?.knowledge?.total_retrievals
                                            ? t('mem.stat.avgRecall', { n: (stats.knowledge.total_retrievals / stats.knowledge.total).toFixed(1) })
                                            : t('mem.stat.noRecalls')}
                                    </div>
                                </div>
                                <div className="mem-stat-card">
                                    <div className="mem-stat-value">{stats?.conversations?.total_sessions ?? 0}</div>
                                    <div className="mem-stat-label">{t('mem.stat.sessions')}</div>
                                    <div className="mem-stat-sub">
                                        {formatDateRange(
                                            stats?.conversations?.first_session ?? null,
                                            stats?.conversations?.last_session ?? null,
                                            dateLocale(t),
                                        ) || t('mem.stat.totalTurns', { n: stats?.conversations?.total_turns ?? 0 })}
                                    </div>
                                </div>
                                <div className="mem-stat-card">
                                    <div className="mem-stat-value">{stats?.tools?.total_calls ?? 0}</div>
                                    <div className="mem-stat-label">{t('mem.stat.toolCalls')}</div>
                                    <div className="mem-stat-sub" title={t('mem.stat.toolsNotLogged')}>
                                        {t('mem.stat.toolsExclMemory', { n: stats?.tools?.unique_tools ?? 0 })}
                                    </div>
                                </div>
                                <div className="mem-stat-card">
                                    <div className="mem-stat-value">
                                        {stats?.tools?.overall_success_rate != null && (stats.tools.total_calls ?? 0) > 0
                                            ? `${Math.round(stats.tools.overall_success_rate * 100)}%`
                                            : '\u2014'}
                                    </div>
                                    <div className="mem-stat-label">{t('mem.stat.successRate')}</div>
                                    <div className="mem-stat-sub">
                                        {(stats?.tools?.total_calls ?? 0) > 0
                                            ? t('mem.stat.errors', { n: stats?.tools?.total_errors ?? 0 })
                                            : t('mem.stat.noToolCalls')}
                                    </div>
                                </div>
                            </div>

                            {/* ── 2. Activity Timeline ─────────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title mem-section-title-row">
                                    <span className="mem-section-title-left">
                                        <TrendingUp size={14} /> {t('mem.activity.title')}
                                    </span>
                                    <div className="mem-range-tabs" role="group" aria-label={t('mem.activity.range')}>
                                        {([1, 7, 30] as const).map(d => (
                                            <button
                                                key={d}
                                                className={`mem-range-tab${activityDays === d ? ' active' : ''}`}
                                                onClick={() => setActivityDays(d)}
                                            >
                                                {t(d === 1 ? 'mem.activity.day1' : d === 7 ? 'mem.activity.days7' : 'mem.activity.days30')}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                {activity.length > 0 ? (
                                    <>
                                        <div className="mem-activity-chart">
                                            {activity.map((day, i) => {
                                                const total = day.conversations + day.tool_calls + day.knowledge_added + day.errors;
                                                const showLabel = i === 0 || i === activity.length - 1 || i % 7 === 0;
                                                const dateStr = new Date(day.date + 'T00:00:00').toLocaleDateString(dateLocale(t), { month: 'short', day: 'numeric' });
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
                                                                {day.conversations > 0 && <>{t('mem.activity.conversations', { n: day.conversations })}<br /></>}
                                                                {day.tool_calls > 0 && <>{t('mem.activity.toolCalls', { n: day.tool_calls })}<br /></>}
                                                                {day.knowledge_added > 0 && <>{t('mem.activity.learned', { n: day.knowledge_added })}<br /></>}
                                                                {day.errors > 0 && <>{t('mem.stat.errors', { n: day.errors })}</>}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        <div className="mem-activity-legend">
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot conversations" /> {t('mem.legend.conversations')}
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot tool_calls" /> {t('mem.stat.toolCalls')}
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot knowledge" /> {t('mem.legend.knowledge')}
                                            </span>
                                            <span className="mem-activity-legend-item">
                                                <span className="mem-activity-legend-dot errors" /> {t('mem.legend.errors')}
                                            </span>
                                        </div>
                                    </>
                                ) : (
                                    <div className="mem-empty">
                                        <div className="mem-empty-icon"><TrendingUp size={28} /></div>
                                        <p>{t('mem.activity.empty')}</p>
                                    </div>
                                )}
                            </div>

                            {/* ── 3. Knowledge Browser ─────────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title mem-section-title-row">
                                    <span className="mem-section-title-left">
                                        <Brain size={14} /> {t('mem.knowledge.title')}
                                        {knowledge && knowledge.total > 0 && (
                                            <span className="mem-section-count">{knowledge.total}</span>
                                        )}
                                    </span>
                                    {!showAddForm && !editingId && (
                                        <button
                                            className="mem-section-add-btn"
                                            onClick={() => setShowAddForm(true)}
                                            title={t('mem.knowledge.addTitle')}
                                        >
                                            <Plus size={13} /> {t('mem.knowledge.add')}
                                        </button>
                                    )}
                                </div>

                                {/* Filters */}
                                <div className="mem-filters">
                                    <div className="mem-category-pills" role="group" aria-label={t('mem.knowledge.filterCategory')}>
                                        {ALL_CATEGORIES.map(c => (
                                            <button
                                                key={c}
                                                className={`mem-category-pill ${c}${kCategories.includes(c) ? ' active' : ''}`}
                                                onClick={() => toggleCategory(c)}
                                                aria-pressed={kCategories.includes(c)}
                                            >
                                                {t(CATEGORY_KEYS[c])}
                                            </button>
                                        ))}
                                        {kCategories.length > 0 && (
                                            <button
                                                className="mem-category-pill clear"
                                                onClick={() => { setKCategories([]); setKOffset(0); }}
                                                aria-label={t('mem.knowledge.clearAria')}
                                            >
                                                ✕ {t('mem.knowledge.clear')}
                                            </button>
                                        )}
                                    </div>
                                    <select className="mem-filter-select" value={kContext}
                                        onChange={e => { setKContext(e.target.value); setKOffset(0); }}
                                        aria-label={t('mem.knowledge.filterContext')}>
                                        <option value="">{t.bare('mem.knowledge.allContexts')}</option>
                                        {contexts.map(c => (
                                            <option key={c} value={c}>{c}</option>
                                        ))}
                                    </select>
                                    <select className="mem-filter-select" value={kEntity}
                                        onChange={e => { setKEntity(e.target.value); setKOffset(0); }}
                                        aria-label={t('mem.knowledge.filterEntity')}>
                                        <option value="">{t.bare('mem.knowledge.allEntities')}</option>
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
                                            placeholder={t('mem.knowledge.searchPlaceholder')}
                                            value={kSearch}
                                            onChange={e => handleSearchChange(e.target.value)}
                                            aria-label={t('mem.knowledge.search')}
                                        />
                                    </div>
                                    <label className="mem-filter-toggle" title={t('mem.knowledge.supersededTitle')}>
                                        <input
                                            type="checkbox"
                                            checked={includeSuperseded}
                                            onChange={e => setIncludeSuperseded(e.target.checked)}
                                        />
                                        <span>{t('mem.knowledge.superseded')}</span>
                                    </label>
                                </div>

                                {/* Add / Edit form */}
                                {(showAddForm || editingId) && (
                                    <div className="mem-inline-form">
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.col.content')}</label>
                                            <textarea value={formData.content}
                                                onChange={e => setFormData(prev => ({ ...prev, content: e.target.value }))}
                                                placeholder={t('mem.form.contentPlaceholder')} />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.col.category')}</label>
                                            <select value={formData.category}
                                                onChange={e => setFormData(prev => ({ ...prev, category: e.target.value }))}>
                                                {ALL_CATEGORIES.map(c => (
                                                    <option key={c} value={c}>{t.bare(CATEGORY_KEYS[c])}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.col.context')}</label>
                                            <input type="text" value={formData.context}
                                                onChange={e => setFormData(prev => ({ ...prev, context: e.target.value }))}
                                                placeholder="global" />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.detail.domain')}</label>
                                            <input type="text" value={formData.domain}
                                                onChange={e => setFormData(prev => ({ ...prev, domain: e.target.value }))}
                                                placeholder={t('mem.form.domainPlaceholder')} />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.col.entity')}</label>
                                            <input type="text" value={formData.entity}
                                                onChange={e => setFormData(prev => ({ ...prev, entity: e.target.value }))}
                                                placeholder={t('mem.form.entityPlaceholder')} />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.form.dueAt')}</label>
                                            <input type="text" value={formData.due_at}
                                                onChange={e => setFormData(prev => ({ ...prev, due_at: e.target.value }))}
                                                placeholder={t('mem.form.dueAtPlaceholder')} />
                                        </div>
                                        <div className="mem-inline-form-row">
                                            <label>{t('mem.form.sensitive')}</label>
                                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12 }}>
                                                <input type="checkbox" checked={formData.sensitive}
                                                    onChange={e => setFormData(prev => ({ ...prev, sensitive: e.target.checked }))} />
                                                {t('mem.form.sensitiveHint')}
                                            </label>
                                        </div>
                                        <div className="mem-inline-form-actions">
                                            <button className="btn-secondary"
                                                onClick={() => { setShowAddForm(false); setEditingId(null); }}>
                                                {t('mem.cancel')}
                                            </button>
                                            <button className="btn-primary"
                                                onClick={editingId ? handleEditKnowledge : handleCreateKnowledge}
                                                style={{ padding: '8px 20px', fontSize: 13 }}>
                                                {t(editingId ? 'mem.form.update' : 'mem.form.save')}
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
                                                            {t('mem.col.category')}{renderSortArrow('category')}
                                                        </th>
                                                        <th>{t('mem.col.content')}</th>
                                                        <th onClick={() => handleSort('context')} className={kSort === 'context' ? 'sorted' : ''}>
                                                            {t('mem.col.context')}{renderSortArrow('context')}
                                                        </th>
                                                        <th>{t('mem.col.entity')}</th>
                                                        <th onClick={() => handleSort('confidence')} className={kSort === 'confidence' ? 'sorted' : ''}>
                                                            {t('mem.col.confidence')}{renderSortArrow('confidence')}
                                                        </th>
                                                        <th>{t('mem.col.source')}</th>
                                                        <th onClick={() => handleSort('updated_at')} className={kSort === 'updated_at' ? 'sorted' : ''}>
                                                            {t('mem.col.updated')}{renderSortArrow('updated_at')}
                                                        </th>
                                                        <th onClick={() => handleSort('created_at')} className={kSort === 'created_at' ? 'sorted' : ''}>
                                                            {t('mem.col.created')}{renderSortArrow('created_at')}
                                                        </th>
                                                        <th>{t('mem.col.due')}</th>
                                                        <th className="mem-actions-cell"></th>
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
                                                                    {entry.category === 'error' && entry.source === 'error_auto'
                                                                        ? t('mem.cat.errorLearned')
                                                                        : <Code value={entry.category} keys={CATEGORY_KEYS} />}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                <div className="mem-content-cell" title={entry.content}>
                                                                    {/* Embedding status dot — only show when backend provides the field */}
                                                                    {hasEmbed !== undefined && (
                                                                        <span
                                                                            className={`mem-embed-dot ${hasEmbed ? 'embedded' : 'missing'}`}
                                                                            title={t(hasEmbed ? 'mem.knowledge.embedded' : 'mem.knowledge.embedMissing')}
                                                                        >
                                                                            <Circle size={6} fill="currentColor" />
                                                                        </span>
                                                                    )}
                                                                    {entry.sensitive && (
                                                                        <span className="mem-sensitive-badge" title={t('mem.form.sensitive')}>
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
                                                                        <bdi>{entry.content}</bdi>
                                                                    </span>
                                                                </div>
                                                            </td>
                                                            <td>
                                                                <bdi className="mem-date-cell">{entry.context}</bdi>
                                                            </td>
                                                            <td>
                                                                {entry.entity ? (
                                                                    <span className="mem-entity-link"
                                                                        onClick={(e) => { e.stopPropagation(); setKEntity(entry.entity!); setKOffset(0); }}
                                                                        title={t('mem.knowledge.filterBy', { entity: entry.entity })}>
                                                                        <bdi>{entry.entity}</bdi>
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
                                                                <bdi className="mem-date-cell">{entry.source || '\u2014'}</bdi>
                                                            </td>
                                                            <td>
                                                                <span className="mem-date-cell" title={formatDateFull(entry.updated_at, dateLocale(t))}>
                                                                    {formatRelativeDate(entry.updated_at, t)}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                <span className="mem-date-cell" title={formatDateFull(entry.created_at, dateLocale(t))}>
                                                                    {formatRelativeDate(entry.created_at, t)}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                {entry.due_at ? (
                                                                    <span className={`mem-due-badge ${isOverdue(entry.due_at) ? 'overdue' : 'upcoming'}`}
                                                                        title={formatDateFull(entry.due_at, dateLocale(t))}>
                                                                        {formatRelativeDate(entry.due_at, t)}
                                                                    </span>
                                                                ) : (
                                                                    <span className="mem-due-badge none">{'\u2014'}</span>
                                                                )}
                                                            </td>
                                                            <td className="mem-actions-cell">
                                                                <div className="mem-row-actions" onClick={e => e.stopPropagation()}>
                                                                    <button className="mem-row-action-btn" onClick={() => startEdit(entry)} title={t('mem.row.edit')}>
                                                                        <Pencil size={13} />
                                                                    </button>
                                                                    <button className="mem-row-action-btn" onClick={() => handleToggleSensitive(entry)}
                                                                        title={t(entry.sensitive ? 'mem.row.unmarkSensitive' : 'mem.row.markSensitive')}>
                                                                        {entry.sensitive ? <ShieldOff size={13} /> : <Shield size={13} />}
                                                                    </button>
                                                                    <button className="mem-row-action-btn" onClick={() => copyId(entry.id)} title={t('mem.row.copyId')}>
                                                                        <Copy size={13} />
                                                                    </button>
                                                                    <button
                                                                        className={`mem-row-action-btn delete${confirmDeleteId === entry.id ? ' is-confirm' : ''}`}
                                                                        onClick={() => handleDeleteKnowledge(entry.id)}
                                                                        title={t(confirmDeleteId === entry.id ? 'mem.row.clickAgain' : 'mem.row.delete')}
                                                                        aria-label={t(confirmDeleteId === entry.id ? 'mem.row.confirmDeleteAria' : 'mem.row.deleteAria')}
                                                                    >
                                                                        {confirmDeleteId === entry.id ? <span className="mem-row-delete-label">{t('mem.row.deleteConfirm')}</span> : <Trash2 size={13} />}
                                                                    </button>
                                                                </div>
                                                            </td>
                                                        </tr>
                                                        {/* Expanded detail row */}
                                                        {isExpanded && (
                                                            <tr className="mem-detail-row">
                                                                <td colSpan={10}>
                                                                    <div className="mem-detail-panel">
                                                                        <div className="mem-detail-grid">
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.detail.id')}</span>
                                                                                <span className="mem-detail-value">{entry.id}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.detail.domain')}</span>
                                                                                <bdi className="mem-detail-value">{entry.domain || '\u2014'}</bdi>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.col.source')}</span>
                                                                                <bdi className="mem-detail-value">{entry.source || '\u2014'}</bdi>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.detail.useCount')}</span>
                                                                                <span className="mem-detail-value">{entry.use_count ?? 0}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.col.created')}</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.created_at, dateLocale(t))}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.col.updated')}</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.updated_at, dateLocale(t))}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.detail.lastUsed')}</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.last_used, dateLocale(t))}</span>
                                                                            </div>
                                                                            <div className="mem-detail-field">
                                                                                <span className="mem-detail-label">{t('mem.detail.remindedAt')}</span>
                                                                                <span className="mem-detail-value">{formatDateFull(entry.reminded_at, dateLocale(t))}</span>
                                                                            </div>
                                                                            {entry.superseded_by && (
                                                                                <div className="mem-detail-field">
                                                                                    <span className="mem-detail-label">{t('mem.detail.supersededBy')}</span>
                                                                                    <span className="mem-detail-value mem-detail-chain">{entry.superseded_by}</span>
                                                                                </div>
                                                                            )}
                                                                            {entry.metadata && (
                                                                                <div className="mem-detail-field mem-detail-full">
                                                                                    <span className="mem-detail-label">{t('mem.detail.metadata')}</span>
                                                                                    <span className="mem-detail-value">
                                                                                        <bdi dir="ltr">
                                                                                            {typeof entry.metadata === 'object'
                                                                                                ? JSON.stringify(entry.metadata, null, 2)
                                                                                                : entry.metadata}
                                                                                        </bdi>
                                                                                    </span>
                                                                                </div>
                                                                            )}
                                                                        </div>
                                                                        <div className="mem-detail-content-full">
                                                                            <span className="mem-detail-label">{t('mem.detail.fullContent')}</span>
                                                                            <div className="mem-detail-content-text"><bdi>{entry.content}</bdi></div>
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
                                                {t('mem.page.range', { from: kOffset + 1, to: Math.min(kOffset + kLimit, knowledge.total), total: knowledge.total })}
                                            </span>
                                            <div className="mem-pagination-btns">
                                                <button type="button" className="mem-page-btn" aria-label={t('mem.page.previous')}
                                                    disabled={kOffset === 0}
                                                    onClick={() => setKOffset(Math.max(0, kOffset - kLimit))}>
                                                    <ChevronLeft size={14} />
                                                </button>
                                                <button type="button" className="mem-page-btn" aria-label={t('mem.page.next')}
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
                                        <p>{t(kSearch || kCategories.length > 0 || kContext || kEntity ? 'mem.knowledge.noMatch' : 'mem.knowledge.empty')}</p>
                                    </div>
                                )}

                            </div>

                            {/* ── Bottom two-column layout ─────────── */}
                            <div className="mem-two-col">
                                {/* ── 4. Tool Performance ─────────── */}
                                <div className="mem-section">
                                    <div className="mem-section-title">
                                        <Wrench size={14} /> {t('mem.tools.title')}
                                        <span className="mem-section-note" title={t('mem.tools.noteTitle')}>{t('mem.tools.note')}</span>
                                    </div>
                                    {tools.length > 0 ? (
                                        <div style={{ overflowX: 'auto' }}>
                                            <table className="mem-tool-table">
                                                <thead>
                                                    <tr>
                                                        <th>{t('mem.tools.tool')}</th>
                                                        <th>{t('mem.tools.calls')}</th>
                                                        <th>{t('mem.tools.success')}</th>
                                                        <th>{t('mem.tools.denied')}</th>
                                                        <th>{t('mem.tools.avgTime')}</th>
                                                        <th>{t('mem.detail.lastUsed')}</th>
                                                        <th>{t('mem.tools.lastError')}</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {tools.map(tool => (
                                                        <tr key={tool.tool_name}>
                                                            <td><bdi className="mem-tool-name">{tool.tool_name}</bdi></td>
                                                            <td>{tool.total_calls}</td>
                                                            <td>
                                                                <div className="mem-confidence">
                                                                    <div className="mem-confidence-bar" style={{ width: 40 }}>
                                                                        <div className={`mem-confidence-fill ${successRateClass(tool.success_rate)}`}
                                                                            style={{ width: `${tool.success_rate * 100}%` }} />
                                                                    </div>
                                                                    <span className={`mem-success-rate ${successRateClass(tool.success_rate)}`}>
                                                                        {Math.round(tool.success_rate * 100)}%
                                                                    </span>
                                                                </div>
                                                            </td>
                                                            <td>{tool.denied_count ?? 0}</td>
                                                            <td><bdi dir="ltr">{formatDuration(tool.avg_duration_ms)}</bdi></td>
                                                            <td>
                                                                <span className="mem-date-cell" title={formatDateFull(tool.last_used, dateLocale(t))}>
                                                                    {formatRelativeDate(tool.last_used, t)}
                                                                </span>
                                                            </td>
                                                            <td>
                                                                <bdi className="mem-tool-error" title={tool.last_error || ''}>
                                                                    {tool.last_error || '\u2014'}
                                                                </bdi>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <div className="mem-empty-icon"><Wrench size={28} /></div>
                                            <p>{t('mem.tools.empty')}</p>
                                        </div>
                                    )}
                                </div>

                                {/* ── 6. Upcoming & Overdue ───────── */}
                                <div className="mem-section">
                                    <div className="mem-section-title">
                                        <Clock size={14} /> {t('mem.upcoming.title')}
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
                                                            <div className="mem-temporal-text"><bdi>{item.content}</bdi></div>
                                                            <div className="mem-temporal-date">
                                                                {t(overdue ? 'mem.upcoming.overdue' : 'mem.upcoming.due', { when: formatRelativeDate(item.due_at, t) })}
                                                                {item.context !== 'global' && <> {'\u00B7'} <bdi>{item.context}</bdi></>}
                                                            </div>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <div className="mem-empty-icon"><CheckCircle size={28} /></div>
                                            <p>{t('mem.upcoming.empty')}</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* ── 5. Conversation History ─────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title">
                                    <MessageSquare size={14} /> {t('mem.conv.title')}
                                </div>
                                <div className="mem-conv-search">
                                    <Search size={13} className="search-icon" />
                                    <input
                                        type="text"
                                        placeholder={t('mem.conv.searchPlaceholder')}
                                        value={convSearch}
                                        onChange={e => handleConvSearchChange(e.target.value)}
                                        aria-label={t('mem.conv.search')}
                                    />
                                    {convSearch && (
                                        <button
                                            className="mem-search-clear"
                                            onClick={() => { setConvSearch(''); setConvSearchResults(null); }}
                                            aria-label={t('mem.conv.clearSearch')}
                                        >
                                            <X size={12} />
                                        </button>
                                    )}
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
                                                            <span className={`mem-conv-turn-role ${turn.role}`}><Code value={turn.role} keys={ROLE_KEYS} /></span>
                                                            {' '}<bdi>{convLabel(turn.session_id)}</bdi>
                                                        </div>
                                                        <div className="mem-conv-preview"><bdi>{turn.content}</bdi></div>
                                                    </div>
                                                    <div className="mem-conv-meta">
                                                        <span>{formatRelativeDate(turn.timestamp, t)}</span>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="mem-empty">
                                            <p>{t('mem.conv.noMatch', { query: convSearch })}</p>
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
                                                        <bdi>{convLabel(c.session_id)}</bdi>
                                                        {c.consolidated && (
                                                            <span className="mem-consolidated-badge" title={t('mem.conv.consolidated')}>
                                                                <GitMerge size={11} />
                                                            </span>
                                                        )}
                                                    </div>
                                                    {c.first_message && (
                                                        <div className="mem-conv-preview"><bdi>{c.first_message}</bdi></div>
                                                    )}
                                                </div>
                                                <div className="mem-conv-meta">
                                                    <span>{t.plural('mem.conv.turns', c.turn_count)}</span>
                                                    <span>{formatRelativeDate(c.last_activity, t)}</span>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="mem-empty">
                                        <div className="mem-empty-icon"><MessageSquare size={28} /></div>
                                        <p>{t('mem.conv.empty')}</p>
                                    </div>
                                )}
                            </div>

                            {/* Conversation detail overlay */}
                            {convDetail && (
                                <div className="mem-conv-detail-overlay">
                                    <div className="mem-conv-detail-header">
                                        <h2><bdi>{convLabel(convDetail.sessionId)}</bdi></h2>
                                        <div className="mem-conv-detail-actions">
                                            {sessionTitles.has(convDetail.sessionId) && (
                                                <button className="btn-secondary"
                                                    onClick={() => setCurrentSession(convDetail.sessionId)}>
                                                    {t('mem.conv.openChat')}
                                                </button>
                                            )}
                                            <button className="btn-icon" onClick={() => setConvDetail(null)} aria-label={t('mem.conv.close')}>
                                                <X size={16} />
                                            </button>
                                        </div>
                                    </div>
                                    <div className="mem-conv-detail-body">
                                        {convDetail.turns.map(turn => (
                                            <div key={turn.id} className="mem-conv-turn">
                                                <div className="mem-conv-turn-header">
                                                    <div className={`mem-conv-turn-role ${turn.role}`}>
                                                        <Code value={turn.role} keys={ROLE_KEYS} />
                                                    </div>
                                                    <span className="mem-conv-turn-time" title={formatDateFull(turn.timestamp, dateLocale(t))}>
                                                        {formatRelativeDate(turn.timestamp, t)}
                                                    </span>
                                                </div>
                                                <div className="mem-conv-turn-content">
                                                    <bdi>{turn.content}</bdi>
                                                </div>
                                            </div>
                                        ))}
                                        {convDetail.turns.length === 0 && (
                                            <div className="mem-empty">{t('mem.conv.noTurns')}</div>
                                        )}
                                    </div>
                                </div>
                            )}

                            </>}

                            {/* ── Settings ─────────────────────── */}
                            <div className="mem-section">
                                <div className="mem-section-title">
                                    <Shield size={14} /> {t('mem.settings.title')} <span className="mem-beta" style={{ fontSize: 10, fontWeight: 600, color: 'var(--accent-yellow)', background: 'var(--accent-yellow-dim)', padding: '1px 6px', borderRadius: 4, marginInlineStart: 6, verticalAlign: 'middle' }}>{t('mem.beta')}</span>
                                </div>
                                <div className="mem-setting-row">
                                    <div className="mem-setting-info">
                                        <span className="mem-setting-label">{t('mem.settings.memory')}</span>
                                        <span className="mem-setting-desc">{t('mem.settings.memoryDesc')}</span>
                                    </div>
                                    <button
                                        className={`mem-toggle${memoryEnabled ? ' mem-toggle-on' : ''}`}
                                        disabled={settingsLoading}
                                        onClick={async () => {
                                            if (!memoryEnabled) {
                                                setBetaConfirm('memory');
                                                return;
                                            }
                                            setSettingsLoading(true);
                                            try {
                                                const updated = await memoryApi.updateMemorySettings({ memory_enabled: false });
                                                setMemoryEnabled(updated.memory_enabled);
                                            } catch (err) {
                                                log.system.warn('Failed to disable memory', err);
                                                showToast(t('mem.toast.settingFailed', { error: errorText(err) }), 'error');
                                            } finally {
                                                setSettingsLoading(false);
                                            }
                                        }}
                                        aria-pressed={memoryEnabled}
                                        aria-label={t(memoryEnabled ? 'mem.settings.memoryOn' : 'mem.settings.memoryOff')}
                                    >
                                        <span className="mem-toggle-knob" aria-hidden="true" />
                                    </button>
                                </div>
                                {/* Floating beta confirmation modal */}
                                {betaConfirm && (() => {
                                    const configs: Record<'memory' | 'mcp' | 'discovery', {
                                        title: MessageKey; description: MessageKey; bullets: MessageKey[]; buttonLabel: MessageKey;
                                        onConfirm: () => Promise<void>;
                                    }> = {
                                        memory: {
                                            title: 'mem.beta.memory.title',
                                            description: 'mem.beta.memory.desc',
                                            bullets: ['mem.beta.memory.local', 'mem.beta.memory.lost', 'mem.beta.memory.disable'],
                                            buttonLabel: 'mem.beta.memory.confirm',
                                            onConfirm: async () => {
                                                const updated = await memoryApi.updateMemorySettings({ memory_enabled: true });
                                                setMemoryEnabled(updated.memory_enabled);
                                                showToast(t('mem.toast.memoryEnabled'), 'info');
                                            },
                                        },
                                        mcp: {
                                            title: 'mem.beta.mcp.title',
                                            description: 'mem.beta.mcp.desc',
                                            bullets: ['mem.beta.mcp.read', 'mem.beta.mcp.restart', 'mem.beta.mcp.debug'],
                                            buttonLabel: 'mem.beta.mcp.confirm',
                                            onConfirm: async () => {
                                                const updated = await memoryApi.updateMemorySettings({ mcp_memory_enabled: true });
                                                setMcpMemoryEnabled(updated.mcp_memory_enabled);
                                                showToast(t('mem.toast.mcpEnabled'), 'info');
                                            },
                                        },
                                        discovery: {
                                            title: 'mem.beta.discovery.title',
                                            description: 'mem.beta.discovery.desc',
                                            bullets: ['mem.beta.discovery.collects', 'mem.beta.discovery.scans', 'mem.beta.discovery.local'],
                                            buttonLabel: 'mem.beta.discovery.confirm',
                                            onConfirm: async () => {
                                                const updated = await memoryApi.updateMemorySettings({ system_discovery_consent: true });
                                                setSystemDiscoveryConsent(updated.system_discovery_consent);
                                                if (updated.system_context_error) {
                                                    showToast(t('mem.toast.discoveryScanFailed', { error: updated.system_context_error }), 'error');
                                                } else {
                                                    const stored = updated.system_context_refresh?.stored ?? 0;
                                                    showToast(t('mem.toast.discoveryEnabled', { n: stored }), 'info');
                                                }
                                                loadAll();
                                            },
                                        },
                                    };
                                    const cfg = configs[betaConfirm];
                                    return (
                                    <>
                                        <div
                                            aria-hidden="true"
                                            style={{
                                                position: 'fixed', inset: 0, background: 'var(--bg-modal-overlay)',
                                                zIndex: 9998, backdropFilter: 'blur(4px)',
                                            }}
                                            onClick={() => setBetaConfirm(null)}
                                        />
                                        <div
                                            ref={betaConfirmRef}
                                            role="dialog"
                                            aria-modal="true"
                                            aria-labelledby="mem-beta-confirm-title"
                                            aria-describedby="mem-beta-confirm-desc"
                                            tabIndex={-1}
                                            style={{
                                                position: 'fixed', top: '50%', left: '50%',
                                                transform: 'translate(-50%, -50%)', zIndex: 9999,
                                                background: 'var(--bg-card)', border: '1px solid var(--border)',
                                                borderRadius: 12, padding: '28px 32px', maxWidth: 420,
                                                width: '90vw', boxShadow: 'var(--shadow-lg)',
                                            }}
                                        >
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                                                <span className="mem-beta" style={{
                                                    fontSize: 11, fontWeight: 700, color: 'var(--accent-yellow)',
                                                    background: 'var(--accent-yellow-dim)', padding: '3px 10px',
                                                    borderRadius: 4,
                                                }}>{t('mem.beta')}</span>
                                                <h2 id="mem-beta-confirm-title" style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
                                                    {t(cfg.title)}
                                                </h2>
                                            </div>
                                            <p id="mem-beta-confirm-desc" style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-secondary)', margin: '0 0 8px' }}>
                                                {t(cfg.description)}
                                            </p>
                                            <ul style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)', margin: '0 0 24px', paddingInlineStart: 20 }}>
                                                {cfg.bullets.map((key) => <li key={key}>{t(key)}</li>)}
                                            </ul>
                                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                                                <button
                                                    className="btn-secondary"
                                                    onClick={() => setBetaConfirm(null)}
                                                    style={{ padding: '8px 20px', fontSize: 13, borderRadius: 6 }}
                                                >
                                                    {t('mem.cancel')}
                                                </button>
                                                <button
                                                    disabled={settingsLoading}
                                                    onClick={async () => {
                                                        setSettingsLoading(true);
                                                        try {
                                                            await cfg.onConfirm();
                                                        } catch (err) {
                                                            log.system.warn('Failed to update setting', err);
                                                            showToast(t('mem.toast.settingFailed', { error: errorText(err) }), 'error');
                                                        } finally {
                                                            setSettingsLoading(false);
                                                            setBetaConfirm(null);
                                                        }
                                                    }}
                                                    style={{
                                                        padding: '8px 24px', fontSize: 13, fontWeight: 600,
                                                        borderRadius: 6, border: 'none', cursor: 'pointer',
                                                        background: 'var(--accent-fill)',
                                                        color: 'var(--accent-fill-text)',
                                                    }}
                                                >
                                                    {t(cfg.buttonLabel)}
                                                </button>
                                            </div>
                                        </div>
                                    </>
                                    );
                                })()}
                                <div className="mem-setting-row">
                                    <div className="mem-setting-info">
                                        <span className="mem-setting-label">{t('mem.settings.mcp')}</span>
                                        <span className="mem-setting-desc">{t('mem.settings.mcpDesc')}</span>
                                    </div>
                                    <button
                                        className={`mem-toggle${mcpMemoryEnabled ? ' mem-toggle-on' : ''}`}
                                        disabled={settingsLoading}
                                        onClick={async () => {
                                            if (!mcpMemoryEnabled) {
                                                setBetaConfirm('mcp');
                                                return;
                                            }
                                            setSettingsLoading(true);
                                            try {
                                                const updated = await memoryApi.updateMemorySettings({ mcp_memory_enabled: false });
                                                setMcpMemoryEnabled(updated.mcp_memory_enabled);
                                            } catch (err) {
                                                log.system.warn('Failed to update memory settings', err);
                                                showToast(t('mem.toast.settingFailed', { error: errorText(err) }), 'error');
                                            } finally {
                                                setSettingsLoading(false);
                                            }
                                        }}
                                        aria-label={t('mem.settings.mcpToggle')}
                                        aria-pressed={mcpMemoryEnabled}
                                    >
                                        <span className="mem-toggle-knob" aria-hidden="true" />
                                    </button>
                                </div>
                                <div className="mem-setting-row">
                                    <div className="mem-setting-info">
                                        <span className="mem-setting-label">{t('mem.settings.discovery')}</span>
                                        <span className="mem-setting-desc">{t('mem.settings.discoveryDesc')}</span>
                                    </div>
                                    <button
                                        className={`mem-toggle${systemDiscoveryConsent ? ' mem-toggle-on' : ''}`}
                                        disabled={settingsLoading}
                                        onClick={async () => {
                                            if (!systemDiscoveryConsent) {
                                                setBetaConfirm('discovery');
                                                return;
                                            }
                                            setSettingsLoading(true);
                                            try {
                                                const updated = await memoryApi.updateMemorySettings({
                                                    system_discovery_consent: false,
                                                });
                                                setSystemDiscoveryConsent(updated.system_discovery_consent);
                                            } catch (err) {
                                                log.system.warn('Failed to update system discovery setting', err);
                                                showToast(t('mem.toast.settingFailed', { error: errorText(err) }), 'error');
                                            } finally {
                                                setSettingsLoading(false);
                                            }
                                        }}
                                        aria-label={t('mem.settings.discoveryToggle')}
                                        aria-pressed={systemDiscoveryConsent}
                                    >
                                        <span className="mem-toggle-knob" aria-hidden="true" />
                                    </button>
                                </div>
                                <div className="mem-setting-row mem-setting-row-danger">
                                    <div className="mem-setting-info">
                                        <span className="mem-setting-label">{t('mem.settings.reinit')}</span>
                                        <span className="mem-setting-desc">{t('mem.settings.reinitDesc')}</span>
                                    </div>
                                    {reinitConfirm ? (
                                        <div className="mem-delete-confirm">
                                            <span className="mem-delete-confirm-label">{t('mem.settings.sure')}</span>
                                            <button
                                                className="mem-btn-danger"
                                                disabled={settingsLoading}
                                                onClick={async () => {
                                                    setSettingsLoading(true);
                                                    try {
                                                        const result = await memoryApi.reinitializeMemory();
                                                        if (result.system_context.error) {
                                                            showToast(t('mem.toast.server', { message: result.system_context.error }), 'error');
                                                        } else {
                                                            showToast(
                                                                t('mem.toast.reinitialized', { cleared: result.cleared.knowledge, stored: result.system_context.stored }),
                                                                'info'
                                                            );
                                                        }
                                                        loadAll();
                                                    } catch (err) {
                                                        log.system.warn('Failed to reinitialize memory', err);
                                                        showToast(t('mem.toast.reinitFailed', { error: errorText(err) }), 'error');
                                                    } finally {
                                                        setSettingsLoading(false);
                                                        setReinitConfirm(false);
                                                    }
                                                }}
                                            >
                                                {t('mem.settings.reinitConfirm')}
                                            </button>
                                            <button
                                                className="btn-secondary"
                                                onClick={() => setReinitConfirm(false)}
                                                style={{ padding: '6px 14px', fontSize: 12 }}
                                            >
                                                {t('mem.cancel')}
                                            </button>
                                        </div>
                                    ) : (
                                        <button
                                            className="mem-btn-danger"
                                            disabled={settingsLoading}
                                            onClick={() => setReinitConfirm(true)}
                                        >
                                            {t('mem.settings.reinitButton')}
                                        </button>
                                    )}
                                </div>
                                <div className="mem-setting-row mem-setting-row-danger">
                                    <div className="mem-setting-info">
                                        <span className="mem-setting-label">{t('mem.settings.deleteAll')}</span>
                                        <span className="mem-setting-desc">{t('mem.settings.deleteAllDesc')}</span>
                                    </div>
                                    {deleteConfirm ? (
                                        <div className="mem-delete-confirm">
                                            <span className="mem-delete-confirm-label">{t('mem.settings.sure')}</span>
                                            <button
                                                className="mem-btn-danger"
                                                disabled={settingsLoading}
                                                onClick={async () => {
                                                    setSettingsLoading(true);
                                                    try {
                                                        const result = await memoryApi.clearAllMemory();
                                                        showToast(t('mem.toast.cleared', { knowledge: result.knowledge, conversations: result.conversations }), 'info');
                                                        loadAll();
                                                    } catch (err) {
                                                        log.system.warn('Failed to clear memory', err);
                                                        showToast(t('mem.toast.clearFailed', { error: errorText(err) }), 'error');
                                                    } finally {
                                                        setSettingsLoading(false);
                                                        setDeleteConfirm(false);
                                                    }
                                                }}
                                            >
                                                {t('mem.settings.deleteConfirm')}
                                            </button>
                                            <button
                                                className="btn-secondary"
                                                onClick={() => setDeleteConfirm(false)}
                                                style={{ padding: '6px 14px', fontSize: 12 }}
                                            >
                                                {t('mem.cancel')}
                                            </button>
                                        </div>
                                    ) : (
                                        <button
                                            className="mem-btn-danger"
                                            disabled={settingsLoading}
                                            onClick={() => setDeleteConfirm(true)}
                                        >
                                            {t('mem.settings.deleteButton')}
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* DB size footer */}
                            {stats && (
                                <div className="mem-db-footer">
                                    <span>
                                        {t('mem.footer.database')}: <bdi dir="ltr">{formatSize(stats.db_size_bytes)}</bdi>
                                        {stats.knowledge?.avg_confidence != null && <>
                                            {' \u00B7 '}{t('mem.footer.avgConfidence', { n: stats.knowledge.avg_confidence.toFixed(2) })}
                                        </>}
                                    </span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        {stats.knowledge?.entity_count != null && stats.knowledge.entity_count > 0 && (
                                            <span>{t('mem.footer.entities', { n: stats.knowledge.entity_count })}</span>
                                        )}
                                        {stats.knowledge?.sensitive_count != null && stats.knowledge.sensitive_count > 0 && (
                                            <span><Shield size={10} /> {t('mem.footer.sensitive', { n: stats.knowledge.sensitive_count })}</span>
                                        )}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                    </>)}

                    {/* ── Profile Setup Tab ──────────────────────────── */}
                    {activeTab === 'profile' && (
                        <div className="mem-profile-grid">
                            <p className="mem-tab-intro">{t('mem.profile.intro')}</p>
                            {/* Card A: System Discovery */}
                            <div className="mem-profile-card">
                                <div className="mem-profile-card-title">
                                    <Cpu size={16} /> {t('mem.disc.title')}
                                </div>
                                <p className="mem-profile-desc">{t('mem.disc.desc')}</p>
                                <button
                                    className="btn-primary mem-profile-run-btn"
                                    disabled={discPhase === 'running' || discPhase === 'saving'}
                                    onClick={runDiscovery}
                                >
                                    {discPhase === 'running' ? (
                                        <><div className="mem-spinner-sm" /> {t('mem.running')}</>
                                    ) : (
                                        t('mem.disc.run')
                                    )}
                                </button>

                                {discLogs.length > 0 && (
                                    <div className="mem-log" ref={discLogRef}>
                                        {discLogs.map((line, i) => (
                                            <div key={i} className={`mem-log-line ${line.kind}`}>
                                                {line.server ? <bdi>{line.text}</bdi> : line.text}
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {(discPhase === 'review' || discPhase === 'saving' || discPhase === 'done') && discItems.length > 0 && (
                                    <div className="mem-review-section">
                                        <div className="mem-review-header">{t('mem.review.title')}</div>
                                        <div className="mem-review-list">
                                            {discItems.map((item, i) => (
                                                <label key={i} className="mem-review-item">
                                                    <input
                                                        type="checkbox"
                                                        checked={item._checked}
                                                        disabled={discPhase === 'saving' || discPhase === 'done'}
                                                        onChange={() => {
                                                            setDiscItems(prev => prev.map((it, idx) =>
                                                                idx === i ? { ...it, _checked: !it._checked } : it
                                                            ));
                                                        }}
                                                    />
                                                    <bdi className="mem-review-content">{item.content}</bdi>
                                                    <span className="mem-source-badge">
                                                        {item._source_name ? <bdi>{item._source_name}</bdi> : <Code value={item.category} keys={CATEGORY_KEYS} />}
                                                    </span>
                                                    <span className="mem-conf-badge">{Math.round(item.confidence * 100)}%</span>
                                                </label>
                                            ))}
                                        </div>
                                        <div className="mem-review-actions">
                                            <button
                                                className="btn-primary"
                                                disabled={discPhase === 'saving' || discPhase === 'done' || discItems.filter(i => i._checked).length === 0}
                                                onClick={saveDiscoveryItems}
                                            >
                                                {discPhase === 'saving' ? (
                                                    <><div className="mem-spinner-sm" /> {t('mem.saving')}</>
                                                ) : (
                                                    t('mem.review.saveSelected', { n: discItems.filter(i => i._checked).length })
                                                )}
                                            </button>
                                            <span className="mem-review-select-links">
                                                <button type="button" className="mem-link-btn" onClick={() => setDiscItems(prev => prev.map(i => ({ ...i, _checked: true })))}>
                                                    {t('mem.review.selectAll')}
                                                </button>
                                                {' / '}
                                                <button type="button" className="mem-link-btn" onClick={() => setDiscItems(prev => prev.map(i => ({ ...i, _checked: false })))}>
                                                    {t('mem.review.none')}
                                                </button>
                                            </span>
                                        </div>
                                    </div>
                                )}

                                {discPhase === 'done' && discSavedCount !== null && (
                                    <div className="mem-profile-success">
                                        <CheckCircle size={14} /> {t('mem.disc.saved', { n: discSavedCount })}
                                    </div>
                                )}

                                {discPhase === 'review' && discItems.length === 0 && (
                                    <div className="mem-profile-empty">{t('mem.disc.empty')}</div>
                                )}
                            </div>

                            {/* Card B: AI Profile Inference */}
                            <div className="mem-profile-card">
                                <div className="mem-profile-card-title">
                                    <Sparkles size={16} /> {t('mem.inf.title')}
                                </div>
                                <p className="mem-profile-desc">{t('mem.inf.desc')}</p>
                                <p className="mem-profile-note">{t('mem.inf.note')}</p>
                                <label className="mem-profile-checkbox">
                                    <input
                                        type="checkbox"
                                        checked={infIncludeBrowser}
                                        onChange={e => setInfIncludeBrowser(e.target.checked)}
                                        disabled={infPhase === 'running' || infPhase === 'saving'}
                                    />
                                    {t('mem.inf.browser')}
                                </label>
                                <button
                                    className="btn-primary mem-profile-run-btn"
                                    disabled={infPhase === 'running' || infPhase === 'saving'}
                                    onClick={runInference}
                                >
                                    {infPhase === 'running' ? (
                                        <><div className="mem-spinner-sm" /> {t('mem.running')}</>
                                    ) : (
                                        t('mem.inf.run')
                                    )}
                                </button>

                                {infLogs.length > 0 && (
                                    <div className="mem-log" ref={infLogRef}>
                                        {infLogs.map((line, i) => (
                                            <div key={i} className={`mem-log-line ${line.kind}`}>
                                                {line.server ? <bdi>{line.text}</bdi> : line.text}
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {(infPhase === 'review' || infPhase === 'saving' || infPhase === 'done') && infItems.length > 0 && (
                                    <div className="mem-review-section">
                                        <div className="mem-review-header">{t('mem.review.title')}</div>
                                        <div className="mem-review-list">
                                            {infItems.map((item, i) => (
                                                <label key={i} className="mem-review-item">
                                                    <input
                                                        type="checkbox"
                                                        checked={item._checked}
                                                        disabled={infPhase === 'saving' || infPhase === 'done'}
                                                        onChange={() => {
                                                            setInfItems(prev => prev.map((it, idx) =>
                                                                idx === i ? { ...it, _checked: !it._checked } : it
                                                            ));
                                                        }}
                                                    />
                                                    <bdi className="mem-review-content">{item.content}</bdi>
                                                    <span className={`mem-domain-badge ${DOMAIN_COLORS[item.domain] || 'gray'}`}>
                                                        <Code value={item.domain} keys={DOMAIN_KEYS} />
                                                    </span>
                                                    <span className="mem-conf-badge">{Math.round(item.confidence * 100)}%</span>
                                                </label>
                                            ))}
                                        </div>
                                        <div className="mem-review-actions">
                                            <button
                                                className="btn-primary"
                                                disabled={infPhase === 'saving' || infPhase === 'done' || infItems.filter(i => i._checked).length === 0}
                                                onClick={saveInferenceItems}
                                            >
                                                {infPhase === 'saving' ? (
                                                    <><div className="mem-spinner-sm" /> {t('mem.saving')}</>
                                                ) : (
                                                    t('mem.review.saveSelected', { n: infItems.filter(i => i._checked).length })
                                                )}
                                            </button>
                                            <span className="mem-review-select-links">
                                                <button type="button" className="mem-link-btn" onClick={() => setInfItems(prev => prev.map(i => ({ ...i, _checked: true })))}>
                                                    {t('mem.review.selectAll')}
                                                </button>
                                                {' / '}
                                                <button type="button" className="mem-link-btn" onClick={() => setInfItems(prev => prev.map(i => ({ ...i, _checked: false })))}>
                                                    {t('mem.review.none')}
                                                </button>
                                            </span>
                                        </div>
                                    </div>
                                )}

                                {infPhase === 'done' && infSavedCount !== null && (
                                    <div className="mem-profile-success">
                                        <CheckCircle size={14} /> {t('mem.inf.saved', { n: infSavedCount })}
                                    </div>
                                )}

                                {infPhase === 'review' && infItems.length === 0 && (
                                    <div className="mem-profile-empty">{t('mem.inf.empty')}</div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* ── Goals Tab ──────────────────────────────────────── */}
                    {activeTab === 'goals' && (
                        <div className="mem-goals-tab">
                            <p className="mem-tab-intro">{t('mem.goals.intro')}</p>
                            {goalStoreError && (
                                <div className="mem-advanced-error" role="alert">
                                    <AlertTriangle size={14} /> {t('mem.goals.storeError')}: <bdi>{goalStoreError}</bdi>
                                </div>
                            )}
                            {/* Header stat row */}
                            <div className="mem-goals-stats-row">
                                {(['queued','in_progress','completed','failed'] as const).map(s => (
                                    <div key={s} className={`mem-goals-stat-pill mem-goal-status-${s.replace('_','-')}`}>
                                        <span className="mem-goals-stat-n">{goalStats?.goals[s] ?? 0}</span>
                                        <span className="mem-goals-stat-label">{t(STATUS_KEYS[s])}</span>
                                    </div>
                                ))}
                            </div>

                            {/* Pending approval banner */}
                            {goals.filter(g => g.status === 'pending_approval').length > 0 && (
                                <div className="mem-goals-approval-banner">
                                    <div className="mem-goals-approval-title">
                                        <Zap size={14} /> {t('mem.goals.suggested', { n: goals.filter(g => g.status === 'pending_approval').length })}
                                    </div>
                                    {goals.filter(g => g.status === 'pending_approval').map(goal => (
                                        <div key={goal.id} className="mem-goals-approval-item">
                                            <div className="mem-goals-approval-text">
                                                <strong><bdi>{goal.title}</bdi></strong>
                                                <bdi>{goal.description}</bdi>
                                            </div>
                                            <div className="mem-goals-approval-actions">
                                                <button className="btn-danger-sm" onClick={() => runGoalAction(
                                                    () => memoryApi.rejectGoal(goal.id), 'mem.goals.rejectFailed',
                                                )}>{t('mem.goals.reject')}</button>
                                                <button className="btn-secondary" onClick={() => runGoalAction(
                                                    () => memoryApi.approveGoal(goal.id), 'mem.goals.acceptFailed',
                                                )}>{t('mem.goals.accept')}</button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {/* Add goal button */}
                            <div className="mem-goals-toolbar">
                                <button className="btn-primary" onClick={() => setShowAddGoalForm(v => !v)}>
                                    <Plus size={14} /> {t('mem.goals.new')}
                                </button>
                            </div>

                            {/* Add goal form */}
                            {showAddGoalForm && (
                                <div className="mem-goals-add-form">
                                    <input
                                        className="mem-input"
                                        placeholder={t('mem.goals.titlePlaceholder')}
                                        value={goalForm.title}
                                        onChange={e => setGoalForm(f => ({ ...f, title: e.target.value }))}
                                    />
                                    <textarea
                                        className="mem-input"
                                        placeholder={t('mem.goals.descPlaceholder')}
                                        rows={2}
                                        value={goalForm.description}
                                        onChange={e => setGoalForm(f => ({ ...f, description: e.target.value }))}
                                    />
                                    <div className="mem-goals-form-row">
                                        <select
                                            className="mem-select"
                                            value={goalForm.priority}
                                            onChange={e => setGoalForm(f => ({ ...f, priority: e.target.value as memoryApi.Priority }))}
                                        >
                                            <option value="low">{t.bare('mem.goals.priorityLow')}</option>
                                            <option value="medium">{t.bare('mem.goals.priorityMedium')}</option>
                                            <option value="high">{t.bare('mem.goals.priorityHigh')}</option>
                                        </select>
                                        <button className="btn-primary" disabled={!goalForm.title.trim()} onClick={() => {
                                            runGoalAction(async () => {
                                                await memoryApi.createGoal(goalForm);
                                                setGoalForm({ title: '', description: '', priority: 'medium' });
                                                setShowAddGoalForm(false);
                                            }, 'mem.goals.createFailed');
                                        }}>{t('mem.goals.create')}</button>
                                        <button className="btn-secondary" onClick={() => setShowAddGoalForm(false)}>{t('mem.cancel')}</button>
                                    </div>
                                </div>
                            )}

                            {/* Goal list */}
                            {goalsLoading ? (
                                <div className="mem-loading"><div className="mem-spinner" /> {t('mem.goals.loading')}</div>
                            ) : goals.filter(g => g.status !== 'pending_approval' && g.status !== 'rejected').length === 0 ? (
                                <div className="mem-empty">
                                    <div className="mem-empty-icon"><Target size={32} /></div>
                                    <p>{t('mem.goals.empty')}</p>
                                </div>
                            ) : (
                                <div className="mem-goals-list">
                                    {goals
                                        .filter(g => g.status !== 'pending_approval' && g.status !== 'rejected')
                                        .map(goal => (
                                        <div key={goal.id} className={`mem-goal-card mem-goal-status-${goal.status.replace('_','-')}`}>
                                            <div
                                                className="mem-goal-header"
                                                role="button"
                                                tabIndex={0}
                                                aria-expanded={expandedGoalId === goal.id}
                                                onClick={() => setExpandedGoalId(id => id === goal.id ? null : goal.id)}
                                                onKeyDown={e => {
                                                    if (e.target !== e.currentTarget) return;
                                                    if (e.key === 'Enter' || e.key === ' ') {
                                                        e.preventDefault();
                                                        setExpandedGoalId(id => id === goal.id ? null : goal.id);
                                                    }
                                                }}
                                            >
                                                <div className="mem-goal-header-left">
                                                    <span className={`mem-goal-status-dot status-${goal.status.replace('_','-')}`} />
                                                    <bdi className="mem-goal-title">{goal.title}</bdi>
                                                    <span className={`mem-goal-priority mem-priority-${goal.priority}`}><Code value={goal.priority} keys={PRIORITY_KEYS} /></span>
                                                    {goal.status === 'cancelled' && (
                                                        <span className="mem-goal-priority mem-goal-cancelled-label">{t(STATUS_KEYS.cancelled)}</span>
                                                    )}
                                                    {goal.source !== 'user' && (
                                                        <span className="mem-goal-source-badge">
                                                            <Zap size={10} /> <Code value={goal.source} keys={GOAL_SOURCE_KEYS} />
                                                        </span>
                                                    )}
                                                </div>
                                                <div className="mem-goal-header-right">
                                                    <span className="mem-goal-task-count">
                                                        <ListChecks size={12} />
                                                        <bdi dir="ltr">{goal.tasks.filter(task => task.status === 'completed').length}/{goal.tasks.length}</bdi>
                                                    </span>
                                                    {goal.status === 'queued' && (
                                                        <button className="mem-goal-action-btn mem-goal-cancel-btn"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                runGoalAction(() => memoryApi.cancelGoal(goal.id), 'mem.goals.cancelFailed');
                                                            }}>
                                                            {t('mem.cancel')}
                                                        </button>
                                                    )}
                                                    {confirmGoalDeleteId === goal.id ? (
                                                        <button className="mem-goal-action-btn mem-goal-delete-btn mem-goal-delete-confirm"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                setConfirmGoalDeleteId(null);
                                                                runGoalAction(() => memoryApi.deleteGoal(goal.id), 'mem.goals.deleteFailed');
                                                            }}>
                                                            {t('mem.row.deleteConfirm')}
                                                        </button>
                                                    ) : (
                                                        <button className="mem-goal-action-btn mem-goal-delete-btn"
                                                            aria-label={t('mem.goals.deleteAria', { title: goal.title })}
                                                            onClick={(e) => { e.stopPropagation(); setConfirmGoalDeleteId(goal.id); }}>
                                                            <Trash2 size={12} />
                                                        </button>
                                                    )}
                                                    <ChevronDown size={14} className={expandedGoalId === goal.id ? 'mem-chevron-open' : ''} />
                                                </div>
                                            </div>

                                            {expandedGoalId === goal.id && (
                                                <div className="mem-goal-body">
                                                    {goal.description && (
                                                        <p className="mem-goal-desc"><bdi>{goal.description}</bdi></p>
                                                    )}
                                                    {goal.progress_notes && (
                                                        <p className="mem-goal-progress"><bdi>{goal.progress_notes}</bdi></p>
                                                    )}
                                                    {goal.tasks.length > 0 ? (
                                                        <div className="mem-task-list">
                                                            {goal.tasks.map(task => (
                                                                <div key={task.id} className={`mem-task-row mem-task-status-${task.status}`}>
                                                                    <span className={`mem-task-dot status-${task.status}`} />
                                                                    <bdi className="mem-task-desc">{task.description}</bdi>
                                                                    <span className="mem-task-status-label"><Code value={task.status} keys={STATUS_KEYS} /></span>
                                                                    {task.result && (
                                                                        <bdi className="mem-task-result">{task.result}</bdi>
                                                                    )}
                                                                </div>
                                                            ))}
                                                        </div>
                                                    ) : goal.status === 'queued' || goal.status === 'in_progress' ? (
                                                        <p className="mem-goal-no-tasks">{t('mem.goals.noTasks')}</p>
                                                    ) : null}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>

            {/* Toast container */}
            {toasts.length > 0 && (
                <div className="mem-toast-container" aria-live="polite">
                    {toasts.map(toast => (
                        <div key={toast.id} className={`mem-toast mem-toast-${toast.type}`} role="status">
                            {toast.type === 'success' && <CheckCircle size={14} />}
                            {toast.type === 'error' && <AlertTriangle size={14} />}
                            {toast.type === 'info' && <Brain size={14} />}
                            <span>{toast.text}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
