// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useRef, useCallback } from 'react';
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import { MIN_CONTEXT_SIZE, DEFAULT_MODEL_NAME } from '../../utils/constants';
import { useModelActions } from '../../hooks/useModelActions';
import { useUpdateStatus } from '../../hooks/useUpdateStatus';
import type { SystemStatus, MCPServerStatus } from '../../types';
import { VersionPicker } from '../VersionPicker';
import '../SettingsModal.css';

/** Model server status, context size, custom model id, beta flags, MCP status, version. */
export function AdvancedSettings() {
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [mcpServers, setMcpServers] = useState<MCPServerStatus[]>([]);
    const [showVersionPicker, setShowVersionPicker] = useState(false);
    const updateStatus = useUpdateStatus();

    // Active Model override
    const [customModel, setCustomModel] = useState<string>('');
    const [savedCustomModel, setSavedCustomModel] = useState<string>('');
    const [settingsLoaded, setSettingsLoaded] = useState(false);
    const [savingModel, setSavingModel] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [justSaved, setJustSaved] = useState(false);
    const justSavedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Dynamic Tools (Beta) toggle — #1798
    const [dynamicTools, setDynamicTools] = useState(false);
    const [dynamicToolsLocked, setDynamicToolsLocked] = useState(false);
    const [savingDynamicTools, setSavingDynamicTools] = useState(false);
    const [dynamicToolsError, setDynamicToolsError] = useState<string | null>(null);

    useEffect(() => {
        log.system.info('Checking system status...');
        const t = log.system.time();
        api.getSystemStatus()
            .then((s) => {
                setStatus(s);
                log.system.timed('System status received', t, {
                    lemonade: s.lemonade_running ? 'running' : 'stopped',
                    model: s.model_loaded || 'none',
                    embedding: s.embedding_model_loaded ? 'yes' : 'no',
                    disk: `${s.disk_space_gb}GB free`,
                    memory: s.memory_available_gb != null ? `${s.memory_available_gb}GB available` : 'unknown',
                });
                if (!s.lemonade_running) {
                    log.system.warn('Lemonade Server is NOT running.');
                }
            })
            .catch((err) => {
                log.system.error('Failed to get system status', err);
                setStatus(null);
            })
            .finally(() => setLoading(false));

        api.getMCPRuntimeStatus()
            .then((r) => setMcpServers(r.servers))
            .catch(() => { /* MCP status is non-critical */ });

        api.getSettings()
            .then((s) => {
                const value = s.custom_model ?? '';
                setCustomModel(value);
                setSavedCustomModel(value);
                setDynamicTools(s.dynamic_tools);
                setDynamicToolsLocked(s.dynamic_tools_locked);
            })
            .catch((err) => {
                log.system.error('Failed to load settings', err);
            })
            .finally(() => setSettingsLoaded(true));
    }, []);

    useEffect(() => {
        return () => { if (justSavedTimerRef.current) clearTimeout(justSavedTimerRef.current); };
    }, []);

    const saveCustomModel = useCallback(async () => {
        const trimmed = customModel.trim();
        const payload = trimmed.length > 0 ? trimmed : '';
        setSavingModel(true);
        setSaveError(null);
        setJustSaved(false);
        try {
            log.system.info('Saving custom_model override', { custom_model: payload });
            const updated = await api.updateSettings({ custom_model: payload });
            const nextValue = updated.custom_model ?? '';
            setCustomModel(nextValue);
            setSavedCustomModel(nextValue);
            setJustSaved(true);
            if (justSavedTimerRef.current) clearTimeout(justSavedTimerRef.current);
            justSavedTimerRef.current = setTimeout(() => setJustSaved(false), 2200);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            log.system.error('Failed to save custom_model', err);
            setSaveError(msg);
        } finally {
            setSavingModel(false);
        }
    }, [customModel]);

    const toggleDynamicTools = useCallback(async () => {
        if (dynamicToolsLocked || savingDynamicTools) return;
        const previous = dynamicTools;
        const next = !previous;
        setDynamicTools(next); // optimistic
        setSavingDynamicTools(true);
        setDynamicToolsError(null);
        try {
            log.system.info('Saving dynamic_tools setting', { dynamic_tools: next });
            const updated = await api.updateSettings({ dynamic_tools: next });
            // Trust the server's effective value (env override may win).
            setDynamicTools(updated.dynamic_tools);
            setDynamicToolsLocked(updated.dynamic_tools_locked);
        } catch (err) {
            // No silent fallback: revert the optimistic flip and surface the error.
            const msg = err instanceof Error ? err.message : String(err);
            log.system.error('Failed to save dynamic_tools', err);
            setDynamicTools(previous);
            setDynamicToolsError(msg);
        } finally {
            setSavingDynamicTools(false);
        }
    }, [dynamicTools, dynamicToolsLocked, savingDynamicTools]);

    const customModelDirty = customModel.trim() !== savedCustomModel.trim();

    const modelName = status?.default_model_name ?? DEFAULT_MODEL_NAME;
    const { isLoadingModel, isDownloadingModel, loadModel, downloadModel } = useModelActions(modelName);

    const CTX_PRESETS: Array<{ label: string; value: number }> = [
        { label: '4K', value: 4096 },
        { label: '8K', value: 8192 },
        { label: '16K', value: 16384 },
        { label: '32K', value: 32768 },
    ];
    const currentCtx = status?.model_context_size ?? null;
    const [ctxInput, setCtxInput] = useState<string>('');
    useEffect(() => {
        if (currentCtx != null) setCtxInput(String(currentCtx));
    }, [currentCtx]);

    const parsedCtxSize = (() => {
        const n = parseInt(ctxInput, 10);
        return Number.isFinite(n) && n > 0 ? n : null;
    })();
    const ctxDirty = parsedCtxSize != null && parsedCtxSize !== currentCtx;
    const targetModelForReload = status?.model_loaded ?? modelName;

    const applyCtxSize = useCallback(async () => {
        if (!parsedCtxSize) return;
        log.system.info(`Reloading ${targetModelForReload} with ctx_size=${parsedCtxSize}`);
        await loadModel(targetModelForReload, parsedCtxSize);
    }, [parsedCtxSize, targetModelForReload, loadModel]);

    const version = __APP_VERSION__;

    const wrongModel   = !!(status?.lemonade_running && status.model_loaded && status.expected_model_loaded === false);
    const smallContext = !!(status?.lemonade_running && status.model_loaded && status.context_size_sufficient === false);
    const notDownloaded = !!(status?.lemonade_running && !status.model_loaded && status.model_downloaded === false);
    const needsLoad    = wrongModel || smallContext;

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Advanced</h2>
                <section className="settings-section">
                    <h4>Local model server</h4>
                    {loading ? (
                        <p className="loading-text">Checking system...</p>
                    ) : status ? (
                        <>
                            <div className="status-grid">
                                <StatusRow
                                    label="Model server"
                                    value={status.lemonade_running ? `Running${status.lemonade_version ? ` v${status.lemonade_version}` : ''}` : status.lemonade_error ? 'Status unavailable' : 'Not Running'}
                                    ok={status.lemonade_running}
                                    hint={!status.lemonade_running
                                        ? (status.lemonade_error ? `Could not query it: ${status.lemonade_error}` : (status.start_command ? `Run: ${status.start_command}` : (status.start_instruction ?? 'Not running.')))
                                        : undefined}
                                />
                                <StatusRow
                                    label="Model"
                                    value={status.model_loaded || 'None loaded'}
                                    ok={!!status.model_loaded && status.expected_model_loaded !== false}
                                    hint={!status.model_loaded
                                        ? 'None loaded yet — it loads with the first message.'
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
                                    hint={!status.model_loaded && status.disk_space_gb < 30 ? `Local models need about 6 GB — only ${status.disk_space_gb} GB free` : undefined}
                                />
                                <StatusRow
                                    label="Memory"
                                    value={status.memory_available_gb != null ? `${status.memory_available_gb} GB available` : 'unknown'}
                                    ok={status.memory_available_gb != null && status.memory_available_gb > 2}
                                />
                                {status.processor_name && (
                                    <StatusRow
                                        label="Processor"
                                        value={status.processor_name}
                                        ok={true}
                                    />
                                )}
                            </div>

                            {notDownloaded && (
                                <div className="model-action-row model-action-row--download">
                                    <div className="model-action-info">
                                        <span className="model-action-label">Model not downloaded.</span>
                                        <span className="model-action-desc">
                                            <strong>{modelName}</strong> is GAIA&rsquo;s default local model (about 6 GB).
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

                            {needsLoad && (
                                <div className="model-action-row model-action-row--load">
                                    <div className="model-action-info">
                                        <span className="model-action-label">
                                            {wrongModel ? 'Wrong model loaded.' : 'Context window too small.'}
                                        </span>
                                        <span className="model-action-desc">
                                            Load <strong>{modelName}</strong> with {(MIN_CONTEXT_SIZE / 1024).toFixed(0)}K token context.
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
                            <p>Could not reach GAIA. Check that it is still running.</p>
                        </div>
                    )}
                </section>

                {/* Active Model */}
                <section className="settings-section">
                    <h4>Custom model ID</h4>
                    <p className="model-override-desc">
                        Run any model the local server knows by its ID. Leave empty to use the model chosen under Model.
                    </p>
                    <div className="model-input-row">
                        <input
                            type="text"
                            className={`model-input${savedCustomModel ? ' has-override' : ''}`}
                            placeholder="Use the model chosen under Model"
                            value={customModel}
                            onChange={(e) => { setCustomModel(e.target.value); setSaveError(null); setJustSaved(false); }}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && !savingModel && customModelDirty && settingsLoaded) {
                                    e.preventDefault();
                                    void saveCustomModel();
                                }
                            }}
                            disabled={!settingsLoaded || savingModel}
                            spellCheck={false}
                            autoCapitalize="off"
                            autoCorrect="off"
                            aria-label="Custom model override"
                        />
                        <div className="model-btn-group">
                            <button
                                className={`btn-model-save${justSaved ? ' saved' : ''}`}
                                onClick={() => { void saveCustomModel(); }}
                                disabled={!settingsLoaded || savingModel || (!customModelDirty && !justSaved)}
                                aria-label="Save custom model"
                            >
                                {savingModel ? (
                                    <><Loader2 size={13} className="btn-spinner" /> Saving…</>
                                ) : justSaved ? (
                                    <><CheckCircle2 size={13} /> Saved</>
                                ) : (
                                    'Save'
                                )}
                            </button>
                        </div>
                    </div>
                    <p className="model-status-hint">
                        Accepts a Lemonade model ID (e.g. <code>Qwen3-4B-Instruct-2507-GGUF</code>)
                        {' '}or a HuggingFace ID (e.g. <code>unsloth/Qwen3-4B-GGUF</code>).
                    </p>
                    {saveError && (
                        <div className="model-warning" role="alert">
                            <AlertCircle size={14} />
                            <div className="model-warning-content">
                                <strong>Could not save</strong>
                                <p>{saveError}</p>
                            </div>
                        </div>
                    )}
                </section>

                {/* Context Size */}
                <section className="settings-section">
                    <h4>Context Size</h4>
                    <p className="model-override-desc">
                        Reload the active model with a different context window.
                        Larger contexts use more memory and slow inference;
                        going past the model&rsquo;s training length may degrade quality.
                    </p>
                    <div className="ctx-preset-row">
                        {CTX_PRESETS.map((p) => {
                            const active = parsedCtxSize === p.value;
                            return (
                                <button
                                    key={p.value}
                                    className={`btn-ctx-preset${active ? ' active' : ''}`}
                                    onClick={() => setCtxInput(String(p.value))}
                                    disabled={isLoadingModel}
                                    type="button"
                                >
                                    {p.label}
                                </button>
                            );
                        })}
                    </div>
                    <div className="model-input-row">
                        <input
                            type="number"
                            className="model-input"
                            placeholder={currentCtx != null ? String(currentCtx) : '4096'}
                            value={ctxInput}
                            min={512}
                            step={1024}
                            onChange={(e) => setCtxInput(e.target.value)}
                            disabled={isLoadingModel}
                            aria-label="Context size in tokens"
                        />
                        <div className="model-btn-group">
                            <button
                                className="btn-model-save"
                                onClick={() => { void applyCtxSize(); }}
                                disabled={isLoadingModel || !ctxDirty || !status?.lemonade_running}
                                aria-label="Reload model with new context size"
                            >
                                {isLoadingModel ? (
                                    <><Loader2 size={13} className="btn-spinner" /> Reloading…</>
                                ) : (
                                    'Apply'
                                )}
                            </button>
                        </div>
                    </div>
                    <p className="model-status-hint">
                        Current: <code>{currentCtx != null ? `${currentCtx.toLocaleString()} tokens` : 'unknown'}</code>
                        {status?.model_loaded && <> on <code>{status.model_loaded}</code></>}.
                    </p>
                </section>

                {/* Dynamic Tools (Beta) — #1798 */}
                <section className="settings-section">
                    <h4>Dynamic Tools <span className="beta-badge">BETA</span></h4>
                    <p className="model-override-desc">
                        Trim each turn&rsquo;s tool list to a semantically-matched subset to
                        speed up the first response. Currently affects the Doc Agent only.
                    </p>
                    {/* <label> wraps the row so a click on the text or the track
                        forwards to the visually-hidden checkbox (matches the
                        working connector/grant toggles). */}
                    <label className="setting-row">
                        <span>Enable dynamic tool loading</span>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                checked={dynamicTools}
                                onChange={() => void toggleDynamicTools()}
                                disabled={!settingsLoaded || savingDynamicTools || dynamicToolsLocked}
                                aria-label={dynamicTools ? 'Disable dynamic tools' : 'Enable dynamic tools'}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                    {dynamicToolsLocked && (
                        <p className="model-status-hint">
                            Controlled by <code>GAIA_DYNAMIC_TOOLS</code> &mdash; unset that
                            environment variable to change this here.
                        </p>
                    )}
                    {dynamicToolsError && (
                        <div className="model-warning" role="alert">
                            <AlertCircle size={14} />
                            <div className="model-warning-content">
                                <strong>Could not save</strong>
                                <p>{dynamicToolsError}</p>
                            </div>
                        </div>
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
                        <p>GAIA v{version} <span className="beta-badge">BETA</span></p>
                        <p className="about-sub">Privacy-first AI chat for AMD Ryzen AI PCs.</p>
                        {updateStatus.pinnedVersion && (
                            <p className="about-pinned-notice">
                                Auto-update paused (pinned to {updateStatus.pinnedVersion}).{' '}
                                <button
                                    type="button"
                                    className="about-resume-btn"
                                    onClick={() => {
                                        const bridge = (window as unknown as { gaiaUpdater?: { resumeUpdates: () => Promise<unknown> } }).gaiaUpdater;
                                        void bridge?.resumeUpdates().catch((e) => {
                                            log.system.error('Resume updates failed', e);
                                        });
                                    }}
                                >
                                    Resume updates
                                </button>
                            </p>
                        )}
                    </div>
                    <div className="setting-actions">
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => setShowVersionPicker(true)}
                        >
                            Roll back to a previous version
                        </button>
                    </div>
                </section>

                {showVersionPicker && (
                    <VersionPicker onClose={() => setShowVersionPicker(false)} />
                )}
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
