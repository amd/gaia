// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useRef, useCallback } from 'react';
import { X, AlertTriangle, ExternalLink } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { SystemStatus, Settings } from '../types';
import './SettingsModal.css';

export function SettingsModal() {
    const { setShowSettings, sessions, removeSession } = useChatStore();
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [loading, setLoading] = useState(true);

    // Custom model override state
    const [settings, setSettings] = useState<Settings | null>(null);
    const [customModelInput, setCustomModelInput] = useState('');
    const [modelSaving, setModelSaving] = useState(false);
    const [modelSaved, setModelSaved] = useState(false);
    const [showModelWarning, setShowModelWarning] = useState(false);
    const modelSavedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        log.system.info('Checking system status...');
        const t = log.system.time();

        // Fetch system status and settings in parallel
        Promise.all([
            api.getSystemStatus(),
            api.getSettings(),
        ])
            .then(([s, settingsData]) => {
                setStatus(s);
                setSettings(settingsData);
                setCustomModelInput(settingsData.custom_model || '');
                log.system.timed('System status received', t, {
                    lemonade: s.lemonade_running ? 'running' : 'stopped',
                    model: s.model_loaded || 'none',
                    embedding: s.embedding_model_loaded ? 'yes' : 'no',
                    disk: `${s.disk_space_gb}GB free`,
                    memory: `${s.memory_available_gb}GB available`,
                    customModel: settingsData.custom_model || 'none',
                });
                if (!s.lemonade_running) {
                    log.system.warn('Lemonade Server is NOT running. Chat will not work. Start it with: lemonade-server serve');
                }
                if (!s.model_loaded) {
                    log.system.warn('No model loaded. Download one with: gaia download --agent chat');
                }
            })
            .catch((err) => {
                log.system.error('Failed to get system status (backend not running?)', err);
                setStatus(null);
            })
            .finally(() => setLoading(false));
    }, []);

    // Cleanup timers
    useEffect(() => {
        return () => {
            if (modelSavedTimerRef.current) clearTimeout(modelSavedTimerRef.current);
        };
    }, []);

    // Two-click confirmation for clear-all (replaces window.confirm)
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

    // Save custom model (with warning confirmation flow)
    const handleModelSave = useCallback(async () => {
        const trimmed = customModelInput.trim();
        const isSettingNew = !!trimmed;
        const currentlySet = !!settings?.custom_model;

        // If setting a new model and warning hasn't been confirmed, show warning first
        if (isSettingNew && !showModelWarning) {
            setShowModelWarning(true);
            return;
        }

        setShowModelWarning(false);
        setModelSaving(true);
        try {
            // Send the trimmed value, or empty string to clear
            // (null means "don't change" in the backend)
            const updated = await api.updateSettings({
                custom_model: trimmed || '',
            });
            setSettings(updated);
            setCustomModelInput(updated.custom_model || '');
            setModelSaved(true);
            if (modelSavedTimerRef.current) clearTimeout(modelSavedTimerRef.current);
            modelSavedTimerRef.current = setTimeout(() => setModelSaved(false), 3000);
            log.system.info(
                isSettingNew
                    ? `Custom model set: ${trimmed}`
                    : 'Custom model override cleared'
            );
        } catch (err) {
            log.system.error('Failed to save custom model', err);
        } finally {
            setModelSaving(false);
        }
    }, [customModelInput, settings, showModelWarning]);

    const handleModelClear = useCallback(async () => {
        setCustomModelInput('');
        setShowModelWarning(false);
        setModelSaving(true);
        try {
            // Send empty string (not null) to explicitly clear the override.
            // Null means "field not provided" in Pydantic, empty string means "clear it".
            const updated = await api.updateSettings({ custom_model: '' });
            setSettings(updated);
            setModelSaved(true);
            if (modelSavedTimerRef.current) clearTimeout(modelSavedTimerRef.current);
            modelSavedTimerRef.current = setTimeout(() => setModelSaved(false), 3000);
            log.system.info('Custom model override cleared');
        } catch (err) {
            log.system.error('Failed to clear custom model', err);
        } finally {
            setModelSaving(false);
        }
    }, []);

    // Determine if the save button should be enabled
    const inputTrimmed = customModelInput.trim();
    const hasChanged = inputTrimmed !== (settings?.custom_model || '');
    const canSave = hasChanged && !modelSaving;
    const hasOverride = !!settings?.custom_model;

    const version = __APP_VERSION__;

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
                                    ok={!!status.model_loaded}
                                    hint={!status.model_loaded ? 'Run: gaia init --profile chat' : undefined}
                                />
                                {status.model_size_gb != null && (
                                    <StatusRow label="Model Size" value={`${status.model_size_gb} GB`} ok={true} />
                                )}
                                {status.model_device && (
                                    <StatusRow label="Device" value={status.model_device.toUpperCase()} ok={status.model_device !== 'cpu'} />
                                )}
                                {status.model_context_size != null && (
                                    <StatusRow label="Context Window" value={`${(status.model_context_size / 1024).toFixed(0)}K tokens`} ok={true} />
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
                                {status.tokens_per_second != null && (
                                    <StatusRow label="Inference Speed" value={`${status.tokens_per_second} tok/s`} ok={status.tokens_per_second > 10} />
                                )}
                                {status.time_to_first_token != null && (
                                    <StatusRow label="Time to First Token" value={`${(status.time_to_first_token * 1000).toFixed(0)} ms`} ok={status.time_to_first_token < 1} />
                                )}
                                {status.processor_name && (
                                    <StatusRow
                                        label="Processor"
                                        value={status.processor_name}
                                        ok={status.device_supported !== false}
                                    />
                                )}
                            </div>
                        ) : (
                            <div className="status-error">
                                <p>Could not connect to server</p>
                                <code>gaia chat --ui</code>
                            </div>
                        )}
                    </section>

                    {/* Model Override */}
                    <section className="settings-section">
                        <h4>Model Override</h4>
                        <div className="model-override">
                            <p className="model-override-desc">
                                Use a custom HuggingFace model instead of the default.
                                Import and load the model in the{' '}
                                <a href="http://localhost:8000" target="_blank" rel="noopener noreferrer" className="lemonade-link">
                                    Lemonade App <ExternalLink size={11} />
                                </a>{' '}
                                first, then enter its name here.
                            </p>
                            <div className="model-input-row">
                                <input
                                    type="text"
                                    className={`model-input ${hasOverride ? 'has-override' : ''}`}
                                    value={customModelInput}
                                    onChange={(e) => {
                                        setCustomModelInput(e.target.value);
                                        setShowModelWarning(false);
                                    }}
                                    placeholder="e.g. Qwen3-Coder-30B-A3B-Instruct-GGUF"
                                    spellCheck={false}
                                    disabled={modelSaving}
                                />
                                <div className="model-btn-group">
                                    <button
                                        className={`btn-model-save ${modelSaved ? 'saved' : ''}`}
                                        onClick={handleModelSave}
                                        disabled={!canSave}
                                    >
                                        {modelSaving ? 'Saving...' : modelSaved ? 'Saved' : 'Save'}
                                    </button>
                                    {hasOverride && (
                                        <button
                                            className="btn-model-clear"
                                            onClick={handleModelClear}
                                            disabled={modelSaving}
                                        >
                                            Clear
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Warning banner */}
                            {showModelWarning && (
                                <div className="model-warning">
                                    <AlertTriangle size={16} />
                                    <div className="model-warning-content">
                                        <strong>Custom models are untested</strong>
                                        <p>
                                            This model has not been validated with GAIA and may produce
                                            unexpected results or lack tool-calling support.
                                            Make sure you have already imported and loaded the model in the{' '}
                                            <a href="http://localhost:8000" target="_blank" rel="noopener noreferrer" className="lemonade-link-inline">
                                                Lemonade App
                                            </a>.
                                        </p>
                                        <button className="btn-model-confirm" onClick={handleModelSave}>
                                            I understand, save anyway
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Active override with status indicators */}
                            {hasOverride && !showModelWarning && (
                                <div className="model-status-section">
                                    <div className="model-active-override">
                                        <span className="model-active-dot" />
                                        Active override: <code>{settings?.custom_model}</code>
                                    </div>
                                    {settings?.model_status && (
                                        <div className="model-status-indicators">
                                            <StatusPill ok={settings.model_status.found} label={settings.model_status.found ? 'Found' : 'Not found'} />
                                            <StatusPill ok={settings.model_status.downloaded} label={settings.model_status.downloaded ? 'Downloaded' : 'Not downloaded'} />
                                            <StatusPill ok={settings.model_status.loaded} label={settings.model_status.loaded ? 'Loaded' : 'Not loaded'} />
                                        </div>
                                    )}
                                    {settings?.model_status && !settings.model_status.found && (
                                        <p className="model-status-hint">
                                            Import this model in the{' '}
                                            <a href="http://localhost:8000" target="_blank" rel="noopener noreferrer" className="lemonade-link-inline">
                                                Lemonade App
                                            </a>{' '}
                                            to download and load it.
                                        </p>
                                    )}
                                    {settings?.model_status && settings.model_status.found && !settings.model_status.downloaded && (
                                        <p className="model-status-hint">
                                            Model found but not downloaded. Install it in the{' '}
                                            <a href="http://localhost:8000" target="_blank" rel="noopener noreferrer" className="lemonade-link-inline">
                                                Lemonade App
                                            </a>.
                                        </p>
                                    )}
                                    {settings?.model_status && settings.model_status.downloaded && !settings.model_status.loaded && (
                                        <p className="model-status-hint">
                                            Model downloaded but not loaded. Load it in the{' '}
                                            <a href="http://localhost:8000" target="_blank" rel="noopener noreferrer" className="lemonade-link-inline">
                                                Lemonade App
                                            </a>{' '}
                                            or it will auto-load on next chat.
                                        </p>
                                    )}
                                </div>
                            )}
                        </div>
                    </section>

                    {/* About */}
                    <section className="settings-section">
                        <h4>About</h4>
                        <div className="about-info">
                            <p>GAIA Agent UI v{version} <span className="beta-badge">BETA</span></p>
                            <p className="about-sub">
                                Privacy-first AI chat for AMD Ryzen AI PCs.
                                <br />
                                No data ever leaves your device.
                            </p>
                        </div>
                    </section>

                    {/* Privacy & Data (danger zone at the bottom) */}
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

// ── MCP Servers Section ──────────────────────────────────────────────────────

function MCPServersSection() {
    const [servers, setServers] = useState<MCPServerInfo[]>([]);
    const [catalog, setCatalog] = useState<MCPCatalogEntry[]>([]);
    const [loadingServers, setLoadingServers] = useState(true);
    const [showAddForm, setShowAddForm] = useState(false);
    const [addMode, setAddMode] = useState<'catalog' | 'custom'>('catalog');
    const [selectedCatalogEntry, setSelectedCatalogEntry] = useState<MCPCatalogEntry | null>(null);
    const [formName, setFormName] = useState('');
    const [formCommand, setFormCommand] = useState('');
    const [formArgs, setFormArgs] = useState('');
    const [formEnv, setFormEnv] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadServers = useCallback(async () => {
        try {
            const result = await api.listMCPServers();
            setServers(result.servers);
        } catch (err) {
            log.system.error('Failed to load MCP servers', err);
        } finally {
            setLoadingServers(false);
        }
    }, []);

    useEffect(() => {
        loadServers();
        api.getMCPCatalog()
            .then((r) => setCatalog(r.catalog))
            .catch((err) => log.system.error('Failed to load MCP catalog', err));
    }, [loadServers]);

    const toggleServer = async (server: MCPServerInfo) => {
        try {
            if (server.enabled) {
                await api.disableMCPServer(server.name);
            } else {
                await api.enableMCPServer(server.name);
            }
            await loadServers();
        } catch (err) {
            log.system.error(`Failed to toggle MCP server ${server.name}`, err);
        }
    };

    const deleteServer = async (name: string) => {
        try {
            await api.removeMCPServer(name);
            await loadServers();
        } catch (err) {
            log.system.error(`Failed to remove MCP server ${name}`, err);
        }
    };

    const onCatalogSelect = (entry: MCPCatalogEntry) => {
        setSelectedCatalogEntry(entry);
        setFormName(entry.name);
        setFormCommand(entry.command);
        setFormArgs(entry.args.join(' '));
        // Pre-fill env keys with empty values so user can fill them in
        const envLines = Object.keys(entry.env || {}).map((k) => `${k}=`).join('\n');
        setFormEnv(envLines);
    };

    const resetForm = () => {
        setFormName('');
        setFormCommand('');
        setFormArgs('');
        setFormEnv('');
        setSelectedCatalogEntry(null);
        setError(null);
    };

    const submitAdd = async () => {
        if (!formName.trim() || !formCommand.trim()) {
            setError('Name and command are required.');
            return;
        }
        setSaving(true);
        setError(null);
        try {
            const args = formArgs.trim() ? formArgs.trim().split(/\s+/) : [];
            const env: Record<string, string> = {};
            for (const line of formEnv.split('\n')) {
                const eq = line.indexOf('=');
                if (eq > 0) {
                    const key = line.slice(0, eq).trim();
                    const val = line.slice(eq + 1).trim();
                    if (key) env[key] = val;
                }
            }
            await api.addMCPServer({
                name: formName.trim(),
                command: formCommand.trim(),
                args,
                env: Object.keys(env).length > 0 ? env : undefined,
            });
            resetForm();
            setShowAddForm(false);
            await loadServers();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Failed to add server');
        } finally {
            setSaving(false);
        }
    };

    return (
        <section className="settings-section">
            <div className="mcp-section-header">
                <h4>MCP Servers</h4>
                <button
                    className="mcp-add-btn"
                    onClick={() => { setShowAddForm((v) => !v); resetForm(); }}
                    aria-label="Add MCP server"
                >
                    <Plus size={14} />
                    Add
                </button>
            </div>
            <p className="mcp-subtitle">Connect external tools to extend agent capabilities.</p>

            {loadingServers ? (
                <p className="loading-text">Loading servers...</p>
            ) : (
                <div className="mcp-server-list">
                    {servers.length === 0 && !showAddForm && (
                        <p className="mcp-empty">No MCP servers configured.</p>
                    )}
                    {servers.map((server) => (
                        <div key={server.name} className={`mcp-server-row${server.enabled ? '' : ' mcp-server-disabled'}`}>
                            <div className="mcp-server-info">
                                <span className="mcp-server-name">{server.name}</span>
                                <span className="mcp-server-cmd">{server.command} {server.args.slice(0, 2).join(' ')}{server.args.length > 2 ? ' …' : ''}</span>
                            </div>
                            <div className="mcp-server-actions">
                                <button
                                    className={`mcp-toggle-btn${server.enabled ? ' mcp-toggle-on' : ''}`}
                                    onClick={() => toggleServer(server)}
                                    title={server.enabled ? 'Disable server' : 'Enable server'}
                                    aria-label={server.enabled ? `Disable ${server.name}` : `Enable ${server.name}`}
                                >
                                    <Power size={13} />
                                </button>
                                <button
                                    className="mcp-delete-btn"
                                    onClick={() => deleteServer(server.name)}
                                    title="Remove server"
                                    aria-label={`Remove ${server.name}`}
                                >
                                    <Trash2 size={13} />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {showAddForm && (
                <div className="mcp-add-form">
                    <div className="mcp-add-mode-tabs">
                        <button
                            className={`mcp-mode-tab${addMode === 'catalog' ? ' active' : ''}`}
                            onClick={() => { setAddMode('catalog'); resetForm(); }}
                        >
                            From catalog
                        </button>
                        <button
                            className={`mcp-mode-tab${addMode === 'custom' ? ' active' : ''}`}
                            onClick={() => { setAddMode('custom'); resetForm(); }}
                        >
                            Custom
                        </button>
                    </div>

                    {addMode === 'catalog' && (
                        <div className="mcp-catalog-list">
                            {catalog.map((entry) => (
                                <button
                                    key={entry.name}
                                    className={`mcp-catalog-entry${selectedCatalogEntry?.name === entry.name ? ' selected' : ''}`}
                                    onClick={() => onCatalogSelect(entry)}
                                >
                                    <div className="mcp-catalog-entry-top">
                                        <span className="mcp-catalog-name">{entry.display_name}</span>
                                        <span className="mcp-catalog-tier">Tier {entry.tier}</span>
                                    </div>
                                    <span className="mcp-catalog-desc">{entry.description}</span>
                                </button>
                            ))}
                        </div>
                    )}

                    <div className="mcp-form-fields">
                        <label className="mcp-field">
                            <span>Name</span>
                            <input
                                type="text"
                                value={formName}
                                onChange={(e) => setFormName(e.target.value)}
                                placeholder="e.g. github"
                                className="mcp-input"
                            />
                        </label>
                        <label className="mcp-field">
                            <span>Command</span>
                            <input
                                type="text"
                                value={formCommand}
                                onChange={(e) => setFormCommand(e.target.value)}
                                placeholder="e.g. npx"
                                className="mcp-input"
                            />
                        </label>
                        <label className="mcp-field">
                            <span>Arguments</span>
                            <input
                                type="text"
                                value={formArgs}
                                onChange={(e) => setFormArgs(e.target.value)}
                                placeholder="-y @modelcontextprotocol/server-github"
                                className="mcp-input"
                            />
                        </label>
                        <label className="mcp-field">
                            <span>Env vars <span className="mcp-field-hint">(KEY=value, one per line)</span></span>
                            <textarea
                                value={formEnv}
                                onChange={(e) => setFormEnv(e.target.value)}
                                placeholder="GITHUB_TOKEN=ghp_xxx"
                                className="mcp-textarea"
                                rows={2}
                            />
                        </label>
                        {error && <p className="mcp-error">{error}</p>}
                        <div className="mcp-form-actions">
                            <button className="btn-secondary" onClick={() => { setShowAddForm(false); resetForm(); }}>
                                Cancel
                            </button>
                            <button className="btn-primary" onClick={submitAdd} disabled={saving}>
                                {saving ? 'Adding…' : 'Add Server'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </section>
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

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
    return (
        <span className={`model-status-pill ${ok ? 'ok' : 'warn'}`}>
            <span className="model-status-pill-dot" />
            {label}
        </span>
    );
}
