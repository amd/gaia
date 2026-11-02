// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useRef, useCallback, type ReactNode } from 'react';
import { ChevronDown, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import { MIN_CONTEXT_SIZE, DEFAULT_MODEL_NAME } from '../../utils/constants';
import { useModelActions } from '../../hooks/useModelActions';
import { formatGb } from '../../utils/modelLabels';
import { useT } from '../../i18n/useT';
import type { SystemStatus, MCPServerStatus } from '../../types';
import { LemonadeServerSection } from './LemonadeServerSection';
import { namesSetupCommand, needsLocalSetup, SetUpLocalButton } from './ErrorNotice';
import { ModelServerPanel } from './ModelServerPanel';
import { SettingsDisclosure } from './SettingsDisclosure';
import '../SettingsModal.css';

/** Model server status and choice, context size, custom model id, beta flags, MCP status, version. */
export function AdvancedSettings() {
    const t = useT();
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [mcpServers, setMcpServers] = useState<MCPServerStatus[]>([]);

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

    // null: an unreadable config hides the model; the banner shows config_error.
    const modelName = status ? status.default_model_name : DEFAULT_MODEL_NAME;
    const { isLoadingModel, isDownloadingModel, loadModel, downloadModel } = useModelActions(modelName ?? undefined);

    const version = __APP_VERSION__;

    // A cloud model runs off this PC: no device, download, load or context to manage.
    const cloudModel   = !!status?.default_model_is_cloud;
    const wrongModel   = !cloudModel && !!(status?.lemonade_running && status.model_loaded && status.expected_model_loaded === false);
    const smallContext = !cloudModel && !!(status?.lemonade_running && status.model_loaded && status.context_size_sufficient === false);
    const notDownloaded = !cloudModel && !!(status?.lemonade_running && !status.model_loaded && status.model_downloaded === false);
    const needsLoad    = (wrongModel || smallContext) && modelName !== null;

    const failedMcp = mcpServers.some((s) => !s.connected);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.advanced')}</h2>
            <section className="settings-card" aria-label={t('settings.advanced.status')}>
                {loading ? (
                    <p className="loading-text">{t('settings.advanced.checking')}</p>
                ) : status ? (
                    <>
                        <div className="status-grid">
                            <StatusRow
                                label={t('settings.advanced.modelServer')}
                                value={status.lemonade_running
                                    ? (status.lemonade_version ? t('settings.advanced.runningAt', { version: status.lemonade_version }) : t('settings.advanced.running'))
                                    : needsLocalSetup(status) ? t('settings.advanced.notInstalled')
                                    : status.lemonade_error ? t('settings.advanced.unavailable') : t('settings.advanced.notRunning')}
                                ok={status.lemonade_running}
                                hint={!status.lemonade_running
                                    ? (needsLocalSetup(status) ? t('settings.advanced.notInstalledHint')
                                        : status.lemonade_error ? t('settings.advanced.queryFailed', { reason: status.lemonade_error })
                                        : status.start_instruction && !namesSetupCommand(status.start_instruction) ? status.start_instruction : t('settings.advanced.notRunningHint'))
                                    : undefined}
                            />
                            {needsLocalSetup(status) && <SetUpLocalButton className="btn-primary" />}
                            <StatusRow
                                label={t('settings.section.model')}
                                value={status.model_loaded || t('settings.advanced.noneLoaded')}
                                ok={!!status.model_loaded && (cloudModel || status.expected_model_loaded !== false)}
                                hint={!status.model_loaded
                                    ? t('settings.advanced.loadsFirst')
                                    : !cloudModel && status.expected_model_loaded === false
                                    ? t('settings.advanced.expected', { name: modelName ?? '' })
                                    : undefined}
                            />
                            <StatusRow
                                label={t('settings.advanced.disk')}
                                value={<bdi dir="ltr">{status.disk_space_gb} GB</bdi>}
                                ok={status.disk_space_gb > 5}
                                hint={!status.model_loaded && status.disk_space_gb < 30 ? t('settings.advanced.diskLow', { size: `${status.disk_space_gb} GB` }) : undefined}
                            />
                        </div>

                        {notDownloaded && (
                            <div className="model-action-row model-action-row--download">
                                <div className="model-action-info">
                                    <span className="model-action-label">{t('settings.advanced.notDownloaded')}</span>
                                    <span className="model-action-desc">
                                        {t('settings.advanced.notDownloadedHint', { name: modelName ?? '' })}
                                    </span>
                                </div>
                                <button
                                    className="btn-model-action btn-model-action--download"
                                    onClick={() => downloadModel(false)}
                                    disabled={isDownloadingModel}
                                >
                                    {isDownloadingModel ? (
                                        <><Loader2 size={13} className="btn-spinner" /> {t('settings.advanced.downloading')}</>
                                    ) : (
                                        t('settings.model.download')
                                    )}
                                </button>
                            </div>
                        )}

                        {needsLoad && (
                            <div className="model-action-row model-action-row--load">
                                <div className="model-action-info">
                                    <span className="model-action-label">
                                        {wrongModel ? t('settings.advanced.wrongModel') : t('settings.advanced.smallContext')}
                                    </span>
                                    <span className="model-action-desc">
                                        {t('settings.advanced.loadHint', { name: modelName ?? '', size: `${(MIN_CONTEXT_SIZE / 1024).toFixed(0)}K` })}
                                    </span>
                                </div>
                                <button
                                    className="btn-model-action btn-model-action--load"
                                    onClick={() => loadModel()}
                                    disabled={isLoadingModel}
                                >
                                    {isLoadingModel ? (
                                        <><Loader2 size={13} className="btn-spinner" /> {t('settings.advanced.loading')}</>
                                    ) : (
                                        t('settings.advanced.load')
                                    )}
                                </button>
                            </div>
                        )}

                        <details className="settings-more">
                            <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.advanced.details')}</summary>
                            <div className="status-grid">
                                {status.model_size_gb != null && (
                                    <StatusRow label={t('settings.advanced.modelSize')} value={<bdi dir="ltr">{status.model_size_gb} GB</bdi>} ok={true} />
                                )}
                                {status.model_device && !cloudModel && (
                                    <StatusRow label={t('settings.advanced.device')} value={status.model_device.toUpperCase()} ok={status.model_device !== 'cpu'} />
                                )}
                                {status.model_context_size != null && (
                                    <StatusRow label={t('settings.advanced.context')} value={t('settings.ctx.tokens', { size: `${(status.model_context_size / 1024).toFixed(0)}K` })} ok={status.context_size_sufficient} />
                                )}
                                {status.model_labels && status.model_labels.length > 0 && (
                                    <StatusRow label={t('settings.advanced.capabilities')} value={status.model_labels.join(', ')} ok={true} />
                                )}
                                <StatusRow label={t('settings.advanced.embedding')} value={status.embedding_model_loaded ? t('settings.advanced.available') : t('settings.advanced.notLoaded')} ok={status.embedding_model_loaded} />
                                {status.gpu_name && (
                                    <StatusRow label="GPU" value={<bdi>{`${status.gpu_name}${status.gpu_vram_gb ? ` (${formatGb(status.gpu_vram_gb)})` : ''}`}</bdi>} ok={true} />
                                )}
                                <StatusRow
                                    label={t('settings.advanced.memory')}
                                    value={status.memory_available_gb != null ? <bdi dir="ltr">{status.memory_available_gb} GB</bdi> : t('settings.advanced.unknown')}
                                    ok={status.memory_available_gb != null && status.memory_available_gb > 2}
                                />
                                {status.processor_name && (
                                    <StatusRow label={t('settings.advanced.processor')} value={<bdi>{status.processor_name}</bdi>} ok={true} />
                                )}
                            </div>
                            {status.lemonade_running && modelName !== null && !cloudModel && (
                                <div className="force-redownload-row">
                                    <span className="force-redownload-label">{t('settings.advanced.corrupted')}</span>
                                    <button
                                        className="btn-force-redownload"
                                        onClick={() => downloadModel(true)}
                                        disabled={isDownloadingModel}
                                    >
                                        {isDownloadingModel ? (
                                            <><Loader2 size={12} className="btn-spinner" /> {t('settings.advanced.downloading')}</>
                                        ) : (
                                            t('settings.advanced.redownload')
                                        )}
                                    </button>
                                </div>
                            )}
                        </details>
                    </>
                ) : (
                    <div className="status-error">
                        <p>{t('settings.advanced.unreachable')}</p>
                    </div>
                )}
            </section>

            <SettingsDisclosure title={t('settings.advanced.serverTitle')} hint={t('settings.advanced.serverHint')}>
                <ModelServerPanel />
                <LemonadeServerSection />
            </SettingsDisclosure>

            <SettingsDisclosure title={t('settings.advanced.customTitle')} hint={savedCustomModel || t('settings.advanced.customHint')}>
                <p className="model-override-desc">{t('settings.advanced.customDesc')}</p>
                <div className="model-input-row">
                    <input
                        type="text"
                        className={`model-input${savedCustomModel ? ' has-override' : ''}`}
                        placeholder={t.bare('settings.advanced.customPlaceholder')}
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
                        aria-label={t('settings.advanced.customAria')}
                    />
                    <div className="model-btn-group">
                        <button
                            className={`btn-model-save${justSaved ? ' saved' : ''}`}
                            onClick={() => { void saveCustomModel(); }}
                            disabled={!settingsLoaded || savingModel || (!customModelDirty && !justSaved)}
                            aria-label={t('settings.advanced.customSave')}
                        >
                            {savingModel ? (
                                <><Loader2 size={13} className="btn-spinner" /> {t('settings.models.applying')}</>
                            ) : justSaved ? (
                                <><CheckCircle2 size={13} /> {t('settings.advanced.saved')}</>
                            ) : (
                                t('settings.advanced.save')
                            )}
                        </button>
                    </div>
                </div>
                <p className="model-status-hint">{t('settings.advanced.customIds')}</p>
                {saveError && <SaveError message={saveError} />}
            </SettingsDisclosure>

            <SettingsDisclosure title={t('settings.advanced.dynamicTitle')}>
                <p className="model-override-desc">{t('settings.advanced.dynamicDesc')}</p>
                {/* <label> wraps the row so a click on the text or the track
                    forwards to the visually-hidden checkbox (matches the
                    working connector/grant toggles). */}
                <label className="setting-row">
                    <span>{t('settings.advanced.dynamicEnable')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={dynamicTools}
                            onChange={() => void toggleDynamicTools()}
                            disabled={!settingsLoaded || savingDynamicTools || dynamicToolsLocked}
                            aria-label={dynamicTools ? t('settings.advanced.dynamicOff') : t('settings.advanced.dynamicOn')}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                {dynamicToolsLocked && <p className="model-status-hint">{t('settings.advanced.dynamicLocked')}</p>}
                {dynamicToolsError && <SaveError message={dynamicToolsError} />}
            </SettingsDisclosure>

            {mcpServers.length > 0 && (
                <SettingsDisclosure title={t('settings.advanced.mcp')} count={mcpServers.length} defaultOpen={failedMcp}>
                    <div className="status-grid">
                        {mcpServers.map((s) => (
                            <div key={s.name} className="status-row">
                                <bdi className="status-label">{s.name}</bdi>
                                <div className="status-value-wrap">
                                    {s.connected ? (
                                        <span className="status-value ok mcp-status-connected">
                                            <CheckCircle2 size={12} />
                                            {t.plural('settings.advanced.tools', s.tool_count)}
                                        </span>
                                    ) : (
                                        <span className="status-value warn mcp-status-failed" title={s.error ?? undefined}>
                                            <AlertCircle size={12} />
                                            {t('settings.custom.failed')}
                                        </span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </SettingsDisclosure>
            )}

            <div className="about-info">
                <p><bdi dir="ltr">GAIA v{version}</bdi> <span className="beta-badge">BETA</span></p>
                <p className="about-sub">{t('settings.advanced.about')}</p>
            </div>
        </div>
    );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function SaveError({ message }: { message: string }) {
    const t = useT();
    return (
        <div className="model-warning" role="alert">
            <AlertCircle size={14} />
            <div className="model-warning-content">
                <strong>{t('settings.advanced.saveFailed')}</strong>
                <p>{message}</p>
            </div>
        </div>
    );
}

function StatusRow({ label, value, ok, hint }: { label: string; value: ReactNode; ok: boolean; hint?: string }) {
    return (
        <div className={`status-row${hint ? ' status-row--has-hint' : ''}`}>
            <span className="status-label">{label}</span>
            <div className="status-value-wrap">
                <span className={`status-value ${ok ? 'ok' : 'warn'}`}>{value}</span>
                {hint && <span className="status-hint">{hint}</span>}
            </div>
        </div>
    );
}
