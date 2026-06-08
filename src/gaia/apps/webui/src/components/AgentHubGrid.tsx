// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { Plus, Search, WifiOff, AlertTriangle, RotateCcw, Package } from 'lucide-react';
import type { AgentInfo, InstallStatus } from '../types';
import { AgentHubCard } from './AgentHubCard';
import { AgentDetailModal } from './AgentDetailModal';
import { InstallConfirmDialog, installWarnings, type InstallWarning } from './InstallConfirmDialog';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import {
    mergeCatalogStatus,
    splitAvailable,
    countUpdates,
    installedTabLabel,
} from '../utils/agentHub';
import './AgentHub.css';

interface AgentHubGridProps {
    agents: AgentInfo[];
    activeAgentId: string;
    onSelect: (id: string) => void;
    onStartChat: (id: string, prompt?: string) => void;
    onCreateAgent?: () => void;
}

// Detect Electron context once
const isElectron = typeof window !== 'undefined' && !!(window as any).electronAPI;

const POLL_INTERVAL_MS = 1200;

type HubTab = 'installed' | 'available';

/** Sort: builtin first, then custom, installed, and native. Alphabetical within groups. */
function sortAgents(agents: AgentInfo[]): AgentInfo[] {
    const order: Record<string, number> = { builtin: 0, custom_python: 1, installed: 2, native: 3 };
    return [...agents].sort((a, b) => {
        const oa = order[a.source] ?? 1;
        const ob = order[b.source] ?? 1;
        if (oa !== ob) return oa - ob;
        return a.name.localeCompare(b.name);
    });
}

function filterAgents(
    agents: AgentInfo[],
    search: string,
    categoryFilter: string,
    tierFilter: string,
): AgentInfo[] {
    let list = agents;
    if (search) {
        const q = search.toLowerCase();
        list = list.filter((a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            (a.tags ?? []).some((t) => t.toLowerCase().includes(q)) ||
            (a.category ?? '').toLowerCase().includes(q)
        );
    }
    if (categoryFilter !== 'all') {
        list = list.filter((a) => a.category === categoryFilter);
    }
    if (tierFilter !== 'all') {
        list = list.filter((a) => (a.security_tier ?? 'experimental') === tierFilter);
    }
    return sortAgents(list);
}

export function AgentHubGrid({ agents, activeAgentId, onSelect, onStartChat, onCreateAgent }: AgentHubGridProps) {
    const [activeTab, setActiveTab] = useState<HubTab>('installed');
    const [search, setSearch] = useState('');
    const [categoryFilter, setCategoryFilter] = useState('all');
    const [tierFilter, setTierFilter] = useState('all');
    const [detailAgent, setDetailAgent] = useState<AgentInfo | null>(null);
    // Agent awaiting an install confirmation (native trust / deprecation), plus
    // the warnings to display.
    const [confirm, setConfirm] = useState<{ agent: AgentInfo; warnings: InstallWarning[] } | null>(null);

    // Catalog state (issue #1096 backend)
    const [catalog, setCatalog] = useState<AgentInfo[]>([]);
    const [catalogOffline, setCatalogOffline] = useState(false);
    const [catalogError, setCatalogError] = useState<string | null>(null);
    const [catalogLoading, setCatalogLoading] = useState(false);
    // True once a catalog fetch has been attempted (success OR failure). Gates
    // the lazy auto-load to a single attempt so a persistent failure doesn't
    // loop; the Retry button re-fetches explicitly.
    const catalogAttemptedRef = useRef(false);
    // True only after a successful catalog load (drives the Installed-tab merge).
    const catalogLoadedRef = useRef(false);

    // Per-agent install progress, keyed by agent id.
    const [installStates, setInstallStates] = useState<Record<string, InstallStatus>>({});
    const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

    const activeDevice = useChatStore((s) => s.activeDevice);
    const setAgents = useChatStore((s) => s.setAgents);

    // ── Catalog fetch ──────────────────────────────────────────────────────
    const fetchCatalog = useCallback(async () => {
        catalogAttemptedRef.current = true;
        setCatalogLoading(true);
        setCatalogError(null);
        try {
            const res = await api.listCatalog();
            setCatalog(res.agents || []);
            setCatalogOffline(!!res.offline);
            catalogLoadedRef.current = true;
        } catch (err) {
            // Fail loudly at the UI boundary — the Available tab shows the error
            // with a Retry; the Installed tab still renders from the local list.
            const msg = err instanceof Error ? err.message : 'Could not load the agent catalog.';
            log.api.warn('[AgentHub] catalog fetch failed', err);
            setCatalogError(msg);
        } finally {
            setCatalogLoading(false);
        }
    }, []);

    // Lazily load the catalog the first time the Available tab is opened.
    useEffect(() => {
        if (activeTab === 'available' && !catalogAttemptedRef.current && !catalogLoading) {
            fetchCatalog();
        }
    }, [activeTab, catalogLoading, fetchCatalog]);

    // Refresh the locally-registered agent list (so installs/uninstalls reflect
    // immediately rather than waiting for the 30s App poll).
    const refreshAgents = useCallback(async () => {
        try {
            const data = await api.listAgents();
            setAgents(data.agents || []);
        } catch (err) {
            log.api.warn('[AgentHub] agent list refresh failed', err);
        }
    }, [setAgents]);

    // ── Install polling ────────────────────────────────────────────────────
    const stopPolling = useCallback((id: string) => {
        const t = pollTimers.current[id];
        if (t) {
            clearInterval(t);
            delete pollTimers.current[id];
        }
    }, []);

    const startPolling = useCallback((id: string) => {
        stopPolling(id);
        pollTimers.current[id] = setInterval(async () => {
            try {
                const status = await api.getInstallStatus(id);
                setInstallStates((s) => ({ ...s, [id]: status }));
                if (status.state === 'installed') {
                    stopPolling(id);
                    await fetchCatalog();
                    await refreshAgents();
                } else if (status.state === 'failed') {
                    stopPolling(id);
                }
            } catch (err) {
                stopPolling(id);
                const msg = err instanceof Error ? err.message : 'Install status check failed.';
                setInstallStates((s) => ({ ...s, [id]: { agent_id: id, state: 'failed', progress: 0, error: msg } }));
            }
        }, POLL_INTERVAL_MS);
    }, [stopPolling, fetchCatalog, refreshAgents]);

    // Cleanup all timers on unmount.
    useEffect(() => () => {
        Object.values(pollTimers.current).forEach(clearInterval);
        pollTimers.current = {};
    }, []);

    const doInstall = useCallback(async (id: string, trustNative: boolean) => {
        setInstallStates((s) => ({ ...s, [id]: { agent_id: id, state: 'downloading', progress: 0 } }));
        try {
            const status = trustNative
                ? await api.installAgent(id, activeDevice, true)
                : await api.installAgent(id, activeDevice);
            setInstallStates((s) => ({ ...s, [id]: status }));
            if (status.state === 'installed') {
                await fetchCatalog();
                await refreshAgents();
            } else if (status.state === 'failed') {
                // leave the failed state for the Retry button
            } else {
                startPolling(id);
            }
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Install failed.';
            setInstallStates((s) => ({ ...s, [id]: { agent_id: id, state: 'failed', progress: 0, error: msg } }));
        }
    }, [activeDevice, startPolling, fetchCatalog, refreshAgents]);

    // Install entry point used by the cards: gate native-trust / deprecated
    // agents behind a confirmation before proceeding.
    const handleInstall = useCallback((id: string) => {
        const agent =
            catalog.find((a) => a.id === id) ?? agents.find((a) => a.id === id);
        const warnings = agent ? installWarnings(agent) : [];
        if (agent && warnings.length > 0) {
            setConfirm({ agent, warnings });
            return;
        }
        void doInstall(id, false);
    }, [catalog, agents, doInstall]);

    const handleConfirmInstall = useCallback(() => {
        if (!confirm) return;
        const id = confirm.agent.id;
        setConfirm(null);
        void doInstall(id, true);
    }, [confirm, doInstall]);

    const handleCancelInstall = useCallback(async (id: string) => {
        stopPolling(id);
        try {
            // No dedicated cancel endpoint yet — uninstall cleans up the partial
            // install. Surface failures rather than silently detaching the poll.
            await api.uninstallAgent(id);
            setInstallStates((s) => { const n = { ...s }; delete n[id]; return n; });
            await fetchCatalog();
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Cancel failed.';
            setInstallStates((s) => ({ ...s, [id]: { agent_id: id, state: 'failed', progress: 0, error: `Cancel failed: ${msg}` } }));
        }
    }, [stopPolling, fetchCatalog]);

    const handleUninstall = useCallback(async (id: string) => {
        const agent = agents.find((a) => a.id === id);
        const name = agent?.name ?? id;
        // eslint-disable-next-line no-alert
        if (!window.confirm(`Uninstall ${name}? You can reinstall it from the Available tab.`)) return;
        try {
            await api.uninstallAgent(id);
            await refreshAgents();
            if (catalogLoadedRef.current) await fetchCatalog();
        } catch (err) {
            log.api.warn('[AgentHub] uninstall failed', err);
            // eslint-disable-next-line no-alert
            window.alert(err instanceof Error ? err.message : 'Uninstall failed.');
        }
    }, [agents, refreshAgents, fetchCatalog]);

    // ── Derived lists ──────────────────────────────────────────────────────
    // Installed tab: local agents merged with catalog status (update badges).
    const installedAgents = useMemo(
        () => mergeCatalogStatus(agents, catalog),
        [agents, catalog],
    );
    const installedIds = useMemo(() => new Set(agents.map((a) => a.id)), [agents]);
    const availableAgents = useMemo(
        () => splitAvailable(catalog, installedIds),
        [catalog, installedIds],
    );
    const updateCount = useMemo(() => countUpdates(installedAgents), [installedAgents]);

    // Category dropdown options for the active tab's source list.
    const sourceForFilter = activeTab === 'installed' ? installedAgents : availableAgents;
    const categories = useMemo(() => {
        const cats = new Set<string>();
        sourceForFilter.forEach((a) => { if (a.category && a.category !== 'general') cats.add(a.category); });
        return Array.from(cats).sort();
    }, [sourceForFilter]);

    const filteredInstalled = useMemo(
        () => filterAgents(installedAgents, search, categoryFilter, tierFilter),
        [installedAgents, search, categoryFilter, tierFilter],
    );
    const filteredAvailable = useMemo(
        () => filterAgents(availableAgents, search, categoryFilter, tierFilter),
        [availableAgents, search, categoryFilter, tierFilter],
    );

    // Security tiers present in the active list (drives the tier dropdown).
    const tiers = useMemo(() => {
        const set = new Set<string>();
        sourceForFilter.forEach((a) => { if (a.security_tier) set.add(a.security_tier); });
        return Array.from(set).sort();
    }, [sourceForFilter]);

    // Search/filter bar: always shown on Available; shown on Installed when 6+.
    const showControls = activeTab === 'available' || installedAgents.length > 6;

    return (
        <div className="agent-hub">
            <div className="agent-hub-header">
                <div className="agent-hub-tabs" role="tablist" aria-label="Agent Hub tabs">
                    <button
                        role="tab"
                        aria-selected={activeTab === 'installed'}
                        className={`agent-hub-tab ${activeTab === 'installed' ? 'active' : ''}`}
                        onClick={() => setActiveTab('installed')}
                    >
                        {installedTabLabel(installedAgents.length, updateCount)}
                    </button>
                    <button
                        role="tab"
                        aria-selected={activeTab === 'available'}
                        className={`agent-hub-tab ${activeTab === 'available' ? 'active' : ''}`}
                        onClick={() => setActiveTab('available')}
                    >
                        Available{availableAgents.length > 0 ? ` (${availableAgents.length})` : ''}
                    </button>
                </div>
                {showControls && (
                    <div className="agent-hub-controls">
                        <div className="agent-hub-search">
                            <Search size={14} />
                            <input
                                type="text"
                                placeholder="Search agents..."
                                aria-label="Search agents"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                            />
                        </div>
                        {categories.length > 0 && (
                            <select
                                className="agent-hub-filter"
                                aria-label="Filter by category"
                                value={categoryFilter}
                                onChange={(e) => setCategoryFilter(e.target.value)}
                            >
                                <option value="all">All categories</option>
                                {categories.map((c) => (
                                    <option key={c} value={c}>{c}</option>
                                ))}
                            </select>
                        )}
                        {tiers.length > 1 && (
                            <select
                                className="agent-hub-filter"
                                aria-label="Filter by security tier"
                                value={tierFilter}
                                onChange={(e) => setTierFilter(e.target.value)}
                            >
                                <option value="all">All tiers</option>
                                {tiers.map((t) => (
                                    <option key={t} value={t}>
                                        {t.charAt(0).toUpperCase() + t.slice(1)}
                                    </option>
                                ))}
                            </select>
                        )}
                    </div>
                )}
            </div>

            {/* Offline / stale-cache banner */}
            {activeTab === 'available' && catalogOffline && (
                <div className="agent-hub-banner agent-hub-banner-offline" role="status">
                    <WifiOff size={14} />
                    <span>Showing cached catalog — the Agent Hub is currently unreachable.</span>
                </div>
            )}

            {/* ── Installed tab ── */}
            {activeTab === 'installed' && (
                <div className="agent-hub-grid">
                    {filteredInstalled.length === 0 && !onCreateAgent && (
                        <div className="agent-hub-empty">No agents match your search.</div>
                    )}
                    {filteredInstalled.map((agent) => (
                        <AgentHubCard
                            key={agent.id}
                            agent={agent}
                            variant="installed"
                            isActive={agent.id === activeAgentId}
                            isElectron={isElectron}
                            onSelect={onSelect}
                            onStartChat={(id) => onStartChat(id)}
                            onViewDetails={setDetailAgent}
                            installStatus={installStates[agent.id]}
                            onInstall={handleInstall}
                            onCancelInstall={handleCancelInstall}
                            onUninstall={handleUninstall}
                        />
                    ))}
                    {onCreateAgent && (
                        <div
                            className="agent-hub-card create-new"
                            role="button"
                            tabIndex={0}
                            onClick={onCreateAgent}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onCreateAgent(); } }}
                        >
                            <div className="create-icon"><Plus size={22} /></div>
                            <div className="create-title">Build a Custom Agent <span className="create-alpha-badge">Alpha</span></div>
                            <div className="create-desc">Create a new agent through conversation</div>
                        </div>
                    )}
                </div>
            )}

            {/* ── Available tab ── */}
            {activeTab === 'available' && (
                <>
                    {catalogLoading && catalog.length === 0 && (
                        <div className="agent-hub-loading">
                            <div className="agent-hub-spinner" />
                            <span>Loading catalog…</span>
                        </div>
                    )}
                    {catalogError && catalog.length === 0 && !catalogLoading && (
                        <div className="agent-hub-banner agent-hub-banner-error" role="alert">
                            <AlertTriangle size={14} />
                            <span>{catalogError}</span>
                            <button className="agent-hub-retry" onClick={fetchCatalog}>
                                <RotateCcw size={13} /> Retry
                            </button>
                        </div>
                    )}
                    {!catalogLoading && !catalogError && (
                        <div className="agent-hub-grid">
                            {filteredAvailable.length === 0 && (
                                <div className="agent-hub-empty">
                                    <Package size={28} strokeWidth={1.5} />
                                    <p>{search || categoryFilter !== 'all'
                                        ? 'No available agents match your search.'
                                        : 'All catalog agents are installed.'}</p>
                                </div>
                            )}
                            {filteredAvailable.map((agent) => {
                                const status = installStates[agent.id];
                                return (
                                    <AgentHubCard
                                        key={agent.id}
                                        agent={agent}
                                        variant="available"
                                        isActive={false}
                                        isElectron={isElectron}
                                        onSelect={onSelect}
                                        onStartChat={(id) => onStartChat(id)}
                                        onViewDetails={setDetailAgent}
                                        installStatus={status}
                                        onInstall={handleInstall}
                                        onCancelInstall={handleCancelInstall}
                                    />
                                );
                            })}
                        </div>
                    )}
                </>
            )}

            {detailAgent && (
                <AgentDetailModal
                    agent={detailAgent}
                    onClose={() => setDetailAgent(null)}
                    onStartChat={onStartChat}
                />
            )}

            {confirm && (
                <InstallConfirmDialog
                    agent={confirm.agent}
                    warnings={confirm.warnings}
                    onConfirm={handleConfirmInstall}
                    onCancel={() => setConfirm(null)}
                />
            )}
        </div>
    );
}
