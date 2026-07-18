// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { Plus, Search, WifiOff, AlertTriangle, RotateCcw, Package } from 'lucide-react';
import type { AgentInfo, InstallStatus } from '../types';
import { AgentHubCard } from './AgentHubCard';
import { AgentDetailModal } from './AgentDetailModal';
import { TrustGateDialog } from './TrustGateDialog';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { mergeCatalogStatus } from '../utils/agentHub';
import { LANES, groupIntoLanes, filterCatalog, trustGateFor, type TrustGate } from '../utils/hubLanes';
import './AgentHub.css';
import './HubPage.css';

interface HubPageProps {
    /** Locally-registered agents (drives per-card installed/active state). */
    agents: AgentInfo[];
    activeAgentId: string;
    onSelect: (id: string) => void;
    onStartChat: (id: string, prompt?: string) => void;
    /** Show the "Build a Custom Agent" tile in the Installed section. */
    onCreateAgent?: () => void;
}

const isElectron = typeof window !== 'undefined' && !!(window as any).electronAPI;
const POLL_INTERVAL_MS = 1200;

/**
 * In-app Hub page (issue #1722): browse the merged agent catalog segmented into
 * Apps · Components · Agents lanes, with one-click install behind a trust gate.
 *
 * The catalog comes from ``GET /api/agents/catalog`` (the local backend merge of
 * the R2 index + local registry). The R2 catalog/publish pipeline
 * (#1717/#1718/#1719) is not live yet, so today the lanes and trust metadata are
 * driven by the local registry merge; when R2 lands it's a backend source swap,
 * not a UI change.
 *
 * Fail-loudly (CLAUDE.md): a failed catalog fetch renders an actionable error
 * with Retry, a failed install leaves a visible failed card with the message,
 * and the trust gate refuses a non-verified install unless explicitly overridden
 * — never a blank page or silent no-op.
 */
export function HubPage({ agents, activeAgentId, onSelect, onStartChat, onCreateAgent }: HubPageProps) {
    const [search, setSearch] = useState('');
    const [detailAgent, setDetailAgent] = useState<AgentInfo | null>(null);
    const [gate, setGate] = useState<{ agent: AgentInfo; gate: TrustGate } | null>(null);

    const [catalog, setCatalog] = useState<AgentInfo[]>([]);
    const [offline, setOffline] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const [installStates, setInstallStates] = useState<Record<string, InstallStatus>>({});
    const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

    const activeDevice = useChatStore((s) => s.activeDevice);
    const setStoreAgents = useChatStore((s) => s.setAgents);
    // Loud discovery failure: if GET /api/agents fails, the installed list can't
    // be trusted — surface it with a Retry instead of a silently-empty grid (#2118).
    const agentsError = useChatStore((s) => s.agentsError);

    // ── Catalog fetch ──────────────────────────────────────────────────────
    const fetchCatalog = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.listCatalog();
            setCatalog(res.agents || []);
            setOffline(!!res.offline);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Could not load the agent catalog.';
            log.api.warn('[HubPage] catalog fetch failed', err);
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchCatalog();
    }, [fetchCatalog]);

    const refreshAgents = useCallback(async () => {
        try {
            const data = await api.listAgents();
            setStoreAgents(data.agents || []);
        } catch (err) {
            log.api.warn('[HubPage] agent list refresh failed', err);
        }
    }, [setStoreAgents]);

    // ── Install polling ────────────────────────────────────────────────────
    const stopPolling = useCallback((id: string) => {
        const t = pollTimers.current[id];
        if (t) {
            clearInterval(t);
            delete pollTimers.current[id];
        }
    }, []);

    const startPolling = useCallback(
        (id: string) => {
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
                    setInstallStates((s) => ({
                        ...s,
                        [id]: { agent_id: id, state: 'failed', progress: 0, error: msg },
                    }));
                }
            }, POLL_INTERVAL_MS);
        },
        [stopPolling, fetchCatalog, refreshAgents],
    );

    useEffect(
        () => () => {
            Object.values(pollTimers.current).forEach(clearInterval);
            pollTimers.current = {};
        },
        [],
    );

    const doInstall = useCallback(
        async (id: string, trustNative: boolean) => {
            setInstallStates((s) => ({ ...s, [id]: { agent_id: id, state: 'downloading', progress: 0 } }));
            try {
                const status = await api.installAgent(id, activeDevice, trustNative);
                setInstallStates((s) => ({ ...s, [id]: status }));
                if (status.state === 'installed') {
                    await fetchCatalog();
                    await refreshAgents();
                } else if (status.state === 'failed') {
                    // leave the failed state visible for the Retry button
                } else {
                    startPolling(id);
                }
            } catch (err) {
                const msg = err instanceof Error ? err.message : 'Install failed.';
                setInstallStates((s) => ({
                    ...s,
                    [id]: { agent_id: id, state: 'failed', progress: 0, error: msg },
                }));
            }
        },
        [activeDevice, startPolling, fetchCatalog, refreshAgents],
    );

    // Every install goes through the trust gate first (issue #1722).
    const handleInstall = useCallback(
        (id: string) => {
            const agent = catalog.find((a) => a.id === id) ?? agents.find((a) => a.id === id);
            if (!agent) return;
            setGate({ agent, gate: trustGateFor(agent) });
        },
        [catalog, agents],
    );

    const handleConfirmInstall = useCallback(
        (trustNative: boolean) => {
            if (!gate) return;
            const id = gate.agent.id;
            setGate(null);
            void doInstall(id, trustNative);
        },
        [gate, doInstall],
    );

    const handleCancelInstall = useCallback(
        async (id: string) => {
            stopPolling(id);
            try {
                await api.uninstallAgent(id);
                setInstallStates((s) => {
                    const n = { ...s };
                    delete n[id];
                    return n;
                });
                await fetchCatalog();
            } catch (err) {
                const msg = err instanceof Error ? err.message : 'Cancel failed.';
                setInstallStates((s) => ({
                    ...s,
                    [id]: { agent_id: id, state: 'failed', progress: 0, error: `Cancel failed: ${msg}` },
                }));
            }
        },
        [stopPolling, fetchCatalog],
    );

    const handleUninstall = useCallback(
        async (id: string) => {
            const agent = agents.find((a) => a.id === id);
            const name = agent?.name ?? id;
            // eslint-disable-next-line no-alert
            if (!window.confirm(`Uninstall ${name}? You can reinstall it from the hub.`)) return;
            try {
                await api.uninstallAgent(id);
                await refreshAgents();
                await fetchCatalog();
            } catch (err) {
                log.api.warn('[HubPage] uninstall failed', err);
                // eslint-disable-next-line no-alert
                window.alert(err instanceof Error ? err.message : 'Uninstall failed.');
            }
        },
        [agents, refreshAgents, fetchCatalog],
    );

    // ── Derived lists ────────────────────────────────────────────────────────
    // Installed section: local agents merged with catalog status (update badges).
    const installedAgents = useMemo(() => mergeCatalogStatus(agents, catalog), [agents, catalog]);
    const filteredInstalled = useMemo(
        () => filterCatalog(installedAgents, search),
        [installedAgents, search],
    );
    const installedIds = useMemo(() => new Set(agents.map((a) => a.id)), [agents]);
    // Only show installable catalog entries here; installed agents are managed
    // elsewhere (the Installed grid). Empty lanes are hidden.
    const available = useMemo(
        () => catalog.filter((a) => a.status === 'available' && !installedIds.has(a.id)),
        [catalog, installedIds],
    );
    const filtered = useMemo(() => filterCatalog(available, search), [available, search]);
    const lanes = useMemo(() => groupIntoLanes(filtered), [filtered]);
    const totalAvailable = filtered.length;

    return (
        <div className="agent-hub hub-page">
            <div className="agent-hub-header hub-page-header">
                <div className="hub-page-title-area">
                    <h1 className="hub-page-title">Agent Hub</h1>
                    <p className="hub-page-subtitle">
                        Browse and install apps, components, and agents.
                    </p>
                </div>
                <div className="agent-hub-controls">
                    <div className="agent-hub-search">
                        <Search size={14} />
                        <input
                            type="text"
                            placeholder="Search the hub..."
                            aria-label="Search the hub"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </div>
            </div>

            {agentsError && (
                <div className="agent-hub-banner agent-hub-banner-error" role="alert">
                    <AlertTriangle size={14} />
                    <span>Couldn’t load installed agents: {agentsError}</span>
                    <button className="agent-hub-retry" onClick={refreshAgents}>
                        <RotateCcw size={13} /> Retry
                    </button>
                </div>
            )}

            {offline && (
                <div className="agent-hub-banner agent-hub-banner-offline" role="status">
                    <WifiOff size={14} />
                    <span>Showing cached catalog — the Agent Hub is currently unreachable.</span>
                </div>
            )}

            {loading && catalog.length === 0 && (
                <div className="agent-hub-loading">
                    <div className="agent-hub-spinner" />
                    <span>Loading catalog…</span>
                </div>
            )}

            {error && catalog.length === 0 && !loading && (
                <div className="agent-hub-banner agent-hub-banner-error" role="alert">
                    <AlertTriangle size={14} />
                    <span>{error}</span>
                    <button className="agent-hub-retry" onClick={fetchCatalog}>
                        <RotateCcw size={13} /> Retry
                    </button>
                </div>
            )}

            {/* ── Installed ── */}
            {(filteredInstalled.length > 0 || onCreateAgent) && (
                <section className="hub-lane" aria-label="Installed">
                    <div className="hub-lane-header">
                        <h2 className="hub-lane-title">
                            Installed <span className="hub-lane-count">{filteredInstalled.length}</span>
                        </h2>
                        <span className="hub-lane-subtitle">Agents ready to use</span>
                    </div>
                    <div className="agent-hub-grid hub-lane-grid">
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
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        onCreateAgent();
                                    }
                                }}
                            >
                                <div className="create-icon">
                                    <Plus size={22} />
                                </div>
                                <div className="create-title">
                                    Build a Custom Agent Template{' '}
                                    <span className="create-alpha-badge">Alpha</span>
                                </div>
                                <div className="create-desc">Create a new agent through conversation</div>
                            </div>
                        )}
                    </div>
                </section>
            )}

            {!loading && !error && totalAvailable === 0 && filteredInstalled.length === 0 && (
                <div className="agent-hub-empty">
                    <Package size={28} strokeWidth={1.5} />
                    <p>
                        {search
                            ? 'No hub items match your search.'
                            : 'All catalog items are installed.'}
                    </p>
                </div>
            )}

            {/* ── Discovery lanes: Apps · Components · Agents ── */}
            {!error &&
                LANES.map((lane) => {
                    const items = lanes[lane.key];
                    if (items.length === 0) return null;
                    return (
                        <section key={lane.key} className="hub-lane" aria-label={lane.title}>
                            <div className="hub-lane-header">
                                <h2 className="hub-lane-title">
                                    {lane.title} <span className="hub-lane-count">{items.length}</span>
                                </h2>
                                <span className="hub-lane-subtitle">{lane.subtitle}</span>
                            </div>
                            <div className="agent-hub-grid hub-lane-grid">
                                {items.map((agent) => (
                                    <AgentHubCard
                                        key={agent.id}
                                        agent={agent}
                                        variant="available"
                                        isActive={agent.id === activeAgentId}
                                        isElectron={isElectron}
                                        onSelect={onSelect}
                                        onStartChat={(id) => onStartChat(id)}
                                        onViewDetails={setDetailAgent}
                                        installStatus={installStates[agent.id]}
                                        onInstall={handleInstall}
                                        onCancelInstall={handleCancelInstall}
                                    />
                                ))}
                            </div>
                        </section>
                    );
                })}

            {detailAgent && (
                <AgentDetailModal
                    agent={detailAgent}
                    onClose={() => setDetailAgent(null)}
                    onStartChat={onStartChat}
                />
            )}

            {gate && (
                <TrustGateDialog
                    agent={gate.agent}
                    gate={gate.gate}
                    onConfirm={handleConfirmInstall}
                    onCancel={() => setGate(null)}
                />
            )}
        </div>
    );
}
