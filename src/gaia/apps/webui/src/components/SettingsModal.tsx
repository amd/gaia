// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useRef, useCallback } from 'react';
import { X, Loader2, CheckCircle2, AlertCircle, Copy, Check, Play, Square } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { MIN_CONTEXT_SIZE, DEFAULT_MODEL_NAME } from '../utils/constants';
import { useModelActions } from '../hooks/useModelActions';
import type { SystemStatus, MCPServerStatus, Settings, AgentMCPServerStatus } from '../types';
import './SettingsModal.css';

// Context-size preset options (tokens).  All must be >= MIN_CONTEXT_SIZE.
const CTX_PRESETS = [
    { label: '32K (minimum)', value: 32768 },
    { label: '64K', value: 65536 },
    { label: '128K', value: 131072 },
    { label: '256K', value: 262144 },
];

export function SettingsModal() {
    const { setShowSettings, sessions, removeSession } = useChatStore();
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [mcpServers, setMcpServers] = useState<MCPServerStatus[]>([]);

    // ── Settings (context size + future overrides) ────────────────────────────
    const [settings, setSettings] = useState<Settings | null>(null);
    const [ctxSize, setCtxSize] = useState<number>(MIN_CONTEXT_SIZE);
    const [ctxSaving, setCtxSaving] = useState(false);
    const [ctxSaved, setCtxSaved] = useState(false);

    // ── Agent UI MCP server ───────────────────────────────────────────────────
    const [agentMCP, setAgentMCP] = useState<AgentMCPServerStatus | null>(null);
    const [mcpPort, setMcpPort] = useState<number>(8765);
    const [mcpBusy, setMcpBusy] = useState(false);
    const [urlCopied, setUrlCopied] = useState(false);

    useEffect(() => {
        log.system.info('Checking system status...');
        const t = log.system.time();

        Promise.all([
            api.getSystemStatus(),
            api.getMCPRuntimeStatus(),
            api.getSettings(),
            api.getAgentMCPServerStatus(),
        ]).then(([sys, mcp, sett, agentSrv]) => {
            setStatus(sys);
            setMcpServers(mcp.servers);
            setSettings(sett);
            setCtxSize(sett.context_size ?? MIN_CONTEXT_SIZE);
            setAgentMCP(agentSrv);
            if (agentSrv.port) setMcpPort(agentSrv.port);
            log.system.timed('Settings modal data loaded', t, {
                lemonade: sys.lemonade_running ? 'running' : 'stopped',
                model: sys.model_loaded || 'none',
                ctx: sett.context_size ?? 'default',
                agentMCP: agentSrv.running ? `running:${agentSrv.port}` : 'stopped',
            });
        }).catch((err) => {
            log.system.error('Failed to load settings modal data', err);
            setStatus(null);
        }).finally(() => setLoading(false));
    }, []);

    const modelName = status?.default_model_name ?? DEFAULT_MODEL_NAME;
    const { isLoadingModel, isDownloadingModel, loadModel, downloadModel } = useModelActions(
        modelName,
        settings?.context_size ?? undefined,
    );

    // ── Context-size save ─────────────────────────────────────────────────────
    const saveCtxSize = useCallback(async () => {
        setCtxSaving(true);
        try {
            const updated = await api.updateSettings({ context_size: ctxSize });
            setSettings(updated);
            setCtxSaved(true);
            log.system.info(`Context size saved: ${ctxSize}`);
            setTimeout(() => setCtxSaved(false), 2000);
        } catch (err) {
            log.system.error('Failed to save context size', err);
        } finally {
            setCtxSaving(false);
        }
    }, [ctxSize]);

    // ── Agent UI MCP server start/stop ────────────────────────────────────────
    const toggleAgentMCP = useCallback(async () => {
        setMcpBusy(true);
        try {
            if (agentMCP?.running) {
                const result = await api.stopAgentMCPServer();
                log.system.info('Agent MCP server stopped', result);
                setAgentMCP((prev) => prev ? { ...prev, running: false, pid: null, url: null } : null);
            } else {
                const result = await api.startAgentMCPServer(mcpPort);
                log.system.info('Agent MCP server started', result);
                setAgentMCP({ running: result.running ?? true, port: result.port, pid: result.pid, url: result.url });
            }
        } catch (err) {
            log.system.error('Failed to toggle Agent MCP server', err);
        } finally {
            setMcpBusy(false);
        }
    }, [agentMCP, mcpPort]);

    const copyMCPUrl = useCallback(() => {
        if (agentMCP?.url) {
            navigator.clipboard.writeText(agentMCP.url).then(() => {
                setUrlCopied(true);
                setTimeout(() => setUrlCopied(false), 2000);
            }).catch(() => {});
        }
    }, [agentMCP]);

    // ── Two-click confirmation for clear-all ──────────────────────────────────
    const [confirmClear, setConfirmClear] = useState(false);
    const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => { if (clearTimerRef.current) clearTimeout(clearTimerRef.current); };
    }, []);

    const clearAll = useCallback(async () => {
        if (!confirmClear) {
            setConfirmClear(true);
            if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
            clearTimerRef.current = setTimeout(() => setConfirmClear(false), 4000);
            return;
        }
        setConfirmClear(false);
        if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
        log.system.warn(`Clearing ALL data: ${sessions.length} session(s)`);
        const t = log.system.time();
        let deleted = 0;
        for (const s of sessions) {
            try {
                await api.deleteSession(s.id);
                removeSession(s.id);
                deleted++;
            } catch (err) {
                log.system.error(`Failed to delete session ${s.id}`, err);
            }
        }
        log.system.timed(`Cleared ${deleted}/${sessions.length} session(s)`, t);
        setShowSettings(false);
    }, [confirmClear, sessions, removeSession, setShowSettings]);

    const version = __APP_VERSION__;

    // Derive model health flags
    const wrongModel   = !!(status?.lemonade_running && status.model_loaded && status.expected_model_loaded === false);
    const smallContext = !!(status?.lemonade_running && status.model_loaded && status.context_size_sufficient === false);
    const notDownloaded = !!(status?.lemonade_running && !status.model_loaded && status.model_downloaded === false);
    const needsLoad    = wrongModel || smallContext;

    // Did the user change the ctx preset from what is currently saved?
    const ctxChanged = ctxSize !== (settings?.context_size ?? MIN_CONTEXT_SIZE);

    return (
        <div className="modal-overlay" onClick={() => setShowSettings(false)} role="dialog" aria-modal="true" aria-label="Settings">
            <div className="modal-panel settings-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Settings</h3>
                    <button className="btn-icon" onClick={() => setShowSettings(false)} aria-label="Close settings">
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body">
                    {/* System Status */}
                    <section className="settings-section">
                        <h4>System Status</h4>
                        {loading ? (
                            <p className="loading-text">Checking system...</p>
                        ) : status ? (
                            <>
                                <div className="status-grid">
                                    <StatusRow
                                        label="Lemonade Server"
                                        value={status.lemonade_running ? `Running${status.lemonade_version ? ` v${status.lemonade_version}` : ''}` : 'Not Running'}
                                        ok={status.lemonade_running}
                                        hint={!status.lemonade_running
                                            ? (status.initialized ? 'Run: lemonade-server serve' : 'Run: gaia init --profile chat')
                                            : undefined}
                                    />
                                    <StatusRow
                                        label="Model"
                                        value={status.model_loaded || 'None loaded'}
                                        ok={!!status.model_loaded && status.expected_model_loaded !== false}
                                        hint={!status.model_loaded
                                            ? 'Run: gaia init --profile chat'
                                            : status.expected_model_loaded === false
                                            ? `Expected: ${modelName}`
                                            : undefined}
                                    />
                                    {status.model_size_gb != null && (
                                        <StatusRow label="Model Size" value={`${status.model_size_gb} GB`} ok={true} />
                                    )}
                                    {status.model_device && (
                                        <StatusRow label="Device" value={status.model_device.toUpperCase()} ok={status.model_device !== 'cpu'} />
                                    )}
                                    {status.model_context_size != null && (
                                        <StatusRow label="Context Window" value={`${(status.model_context_size / 1024).toFixed(0)}K tokens`} ok={status.context_size_sufficient} />
                                    )}
                                    {status.model_labels && status.model_labels.length > 0 && (
                                        <StatusRow label="Capabilities" value={status.model_labels.join(', ')} ok={true} />
                                    )}
                                    <StatusRow label="Embedding Model" value={status.embedding_model_loaded ? 'Available' : 'Not loaded'} ok={status.embedding_model_loaded} />
                                    {status.gpu_name && (
                                        <StatusRow label="GPU" value={`${status.gpu_name}${status.gpu_vram_gb ? ` (${status.gpu_vram_gb} GB)` : ''}`} ok={true} />
                                    )}
                                    <StatusRow
                                        label="Disk Space"
                                        value={`${status.disk_space_gb} GB free`}
                                        ok={status.disk_space_gb > 5}
                                        hint={!status.model_loaded && status.disk_space_gb < 30 ? `Models require ~25 GB — only ${status.disk_space_gb} GB available` : undefined}
                                    />
                                    <StatusRow label="Memory" value={`${status.memory_available_gb} GB available`} ok={status.memory_available_gb > 2} />
                                    {status.processor_name && (
                                        <StatusRow
                                            label="Processor"
                                            value={status.processor_name}
                                            ok={status.device_supported !== false}
                                        />
                                    )}
                                </div>

                                {/* Model not downloaded — offer download */}
                                {notDownloaded && (
                                    <div className="model-action-row model-action-row--download">
                                        <div className="model-action-info">
                                            <span className="model-action-label">Model not downloaded.</span>
                                            <span className="model-action-desc">
                                                <strong>{modelName}</strong> is required for GAIA Chat (~25 GB).
                                            </span>
                                        </div>
                                        <button
                                            className="btn-model-action btn-model-action--download"
                                            onClick={() => downloadModel(false)}
                                            disabled={isDownloadingModel}
                                        >
                                            {isDownloadingModel ? (
                                                <><Loader2 size={13} className="btn-spinner" /> Downloading…</>
                                            ) : (
                                                'Download'
                                            )}
                                        </button>
                                    </div>
                                )}

                                {/* Wrong model or small context — offer load */}
                                {needsLoad && (
                                    <div className="model-action-row model-action-row--load">
                                        <div className="model-action-info">
                                            <span className="model-action-label">
                                                {wrongModel ? 'Wrong model loaded.' : 'Context window too small.'}
                                            </span>
                                            <span className="model-action-desc">
                                                Load <strong>{modelName}</strong> with {(ctxSize / 1024).toFixed(0)}K token context.
                                            </span>
                                        </div>
                                        <button
                                            className="btn-model-action btn-model-action--load"
                                            onClick={() => loadModel()}
                                            disabled={isLoadingModel}
                                        >
                                            {isLoadingModel ? (
                                                <><Loader2 size={13} className="btn-spinner" /> Loading…</>
                                            ) : (
                                                'Load Model'
                                            )}
                                        </button>
                                    </div>
                                )}

                                {/* Force re-download — always visible when Lemonade is running */}
                                {status.lemonade_running && (
                                    <div className="force-redownload-row">
                                        <span className="force-redownload-label">
                                            If the model file is corrupted:
                                        </span>
                                        <button
                                            className="btn-force-redownload"
                                            onClick={() => downloadModel(true)}
                                            disabled={isDownloadingModel}
                                        >
                                            {isDownloadingModel ? (
                                                <><Loader2 size={12} className="btn-spinner" /> Downloading…</>
                                            ) : (
                                                'Force Re-download'
                                            )}
                                        </button>
                                    </div>
                                )}
                            </>
                        ) : (
                            <div className="status-error">
                                <p>Could not connect to server</p>
                                <code>gaia chat --ui</code>
                            </div>
                        )}
                    </section>

                    {/* Context Window Size */}
                    <section className="settings-section">
                        <h4>Context Window</h4>
                        <p className="settings-desc">
                            Set how many tokens the model holds in memory per conversation.
                            Larger contexts let the agent reason over longer history but
                            require more RAM. Minimum is 32K (required by GAIA Chat).
                        </p>
                        <div className="ctx-row">
                            <select
                                className="ctx-select"
                                value={ctxSize}
                                onChange={(e) => setCtxSize(Number(e.target.value))}
                            >
                                {CTX_PRESETS.map((p) => (
                                    <option key={p.value} value={p.value}>{p.label}</option>
                                ))}
                                {/* Show custom entry if stored value doesn't match any preset */}
                                {!CTX_PRESETS.find((p) => p.value === ctxSize) && (
                                    <option value={ctxSize}>{(ctxSize / 1024).toFixed(0)}K (custom)</option>
                                )}
                            </select>
                            <button
                                className={`btn-ctx-save${ctxSaved ? ' saved' : ''}`}
                                onClick={saveCtxSize}
                                disabled={ctxSaving || (!ctxChanged && !ctxSaved)}
                                title="Save context size preference"
                            >
                                {ctxSaving ? (
                                    <><Loader2 size={12} className="btn-spinner" /> Saving…</>
                                ) : ctxSaved ? (
                                    <><Check size={12} /> Saved</>
                                ) : (
                                    'Save'
                                )}
                            </button>
                        </div>
                        {status?.model_loaded && ctxChanged && (
                            <p className="ctx-hint">
                                Save then reload the model to apply the new context size.
                            </p>
                        )}
                    </section>

                    {/* Agent UI MCP Server */}
                    <section className="settings-section">
                        <h4>Agent UI MCP Server</h4>
                        <p className="settings-desc">
                            Expose GAIA Chat as MCP tools so external clients (e.g.,{' '}
                            <strong>Claude Code</strong>) can create sessions, send messages,
                            and browse files directly from their tool call interface.
                        </p>

                        {agentMCP === null ? (
                            <p className="loading-text">Checking MCP server…</p>
                        ) : (
                            <>
                                <div className="mcp-server-row">
                                    <div className="mcp-server-status">
                                        <span className={`mcp-dot${agentMCP.running ? ' mcp-dot--running' : ''}`} />
                                        <span className="mcp-status-text">
                                            {agentMCP.running ? `Running on port ${agentMCP.port}` : 'Stopped'}
                                        </span>
                                    </div>

                                    {!agentMCP.running && (
                                        <div className="mcp-port-wrap">
                                            <label className="mcp-port-label" htmlFor="mcp-port-input">Port</label>
                                            <input
                                                id="mcp-port-input"
                                                className="mcp-port-input"
                                                type="number"
                                                min={1024}
                                                max={65535}
                                                value={mcpPort}
                                                onChange={(e) => setMcpPort(Number(e.target.value))}
                                            />
                                        </div>
                                    )}

                                    <button
                                        className={`btn-mcp-toggle${agentMCP.running ? ' btn-mcp-toggle--stop' : ' btn-mcp-toggle--start'}`}
                                        onClick={toggleAgentMCP}
                                        disabled={mcpBusy}
                                        title={agentMCP.running ? 'Stop MCP server' : 'Start MCP server'}
                                    >
                                        {mcpBusy ? (
                                            <Loader2 size={13} className="btn-spinner" />
                                        ) : agentMCP.running ? (
                                            <><Square size={12} /> Stop</>
                                        ) : (
                                            <><Play size={12} /> Start</>
                                        )}
                                    </button>
                                </div>

                                {agentMCP.running && agentMCP.url && (
                                    <div className="mcp-url-row">
                                        <code className="mcp-url">{agentMCP.url}</code>
                                        <button
                                            className="btn-mcp-copy"
                                            onClick={copyMCPUrl}
                                            title="Copy MCP server URL"
                                        >
                                            {urlCopied ? <Check size={13} /> : <Copy size={13} />}
                                        </button>
                                    </div>
                                )}

                                {agentMCP.running && (
                                    <p className="mcp-connect-hint">
                                        Add to Claude Code: <code>gaia mcp add-to-claude</code>
                                    </p>
                                )}
                            </>
                        )}
                    </section>

                    {/* MCP Servers */}
                    {mcpServers.length > 0 && (
                        <section className="settings-section">
                            <h4>MCP Servers</h4>
                            <div className="status-grid">
                                {mcpServers.map((s) => (
                                    <div key={s.name} className="status-row">
                                        <span className="status-label">{s.name}</span>
                                        <div className="status-value-wrap">
                                            {s.connected ? (
                                                <span className="status-value ok mcp-status-connected">
                                                    <CheckCircle2 size={12} />
                                                    {s.tool_count} tool{s.tool_count !== 1 ? 's' : ''}
                                                </span>
                                            ) : (
                                                <span className="status-value warn mcp-status-failed" title={s.error ?? undefined}>
                                                    <AlertCircle size={12} />
                                                    Failed
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}

                    {/* About */}
                    <section className="settings-section">
                        <h4>About</h4>
                        <div className="about-info">
                            <p>GAIA Agent UI v{version} <span className="beta-badge">BETA</span></p>
                            <p className="about-sub">Privacy-first AI chat for AMD Ryzen AI PCs.</p>
                        </div>
                    </section>

                    {/* Privacy & Data */}
                    <section className="settings-section danger-zone">
                        <h4>Privacy & Data</h4>
                        <div className="setting-row">
                            <span>Data location</span>
                            <code className="setting-path">~/.gaia/chat/</code>
                        </div>
                        <div className="danger-divider" />
                        <div className="setting-actions">
                            <p className="danger-warning">This will permanently delete all sessions, messages, and documents.</p>
                            <button className="btn-danger" onClick={clearAll}>
                                {confirmClear ? 'Click again to confirm' : 'Clear All Data'}
                            </button>
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function StatusRow({ label, value, ok, hint }: { label: string; value: string; ok: boolean; hint?: string }) {
    return (
        <div className={`status-row${hint ? ' status-row--has-hint' : ''}`}>
            <span className="status-label">{label}</span>
            <div className="status-value-wrap">
                <span className={`status-value ${ok ? 'ok' : 'warn'}`}>{value}</span>
                {hint && <span className="status-hint"><code>{hint}</code></span>}
            </div>
        </div>
    );
}
