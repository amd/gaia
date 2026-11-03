// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Environment (#5276): where this chat runs and what it may touch. Every
 * switch here changes what the agent is allowed to do on its next step — the
 * backend enforces it in the agent's path checks and tool policy.
 */

import { useCallback, useEffect, useState } from 'react';
import { Cloud, File, Folder, FolderPlus, Monitor, ShieldAlert, ShieldCheck, Smartphone, X } from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { log } from '../../utils/logger';
import { useInferencePlace, useModelStore, selectChipModel, displayModelName, UNKNOWN_PLACE_TITLE } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import { useFolderPicker } from '../../hooks/useFolderPicker';
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { announcePermissionMode, usePermissionModeEvents } from '../../utils/permissionModeEvents';
import { requestMobileAccess, useMobileAccessState } from '../../utils/mobileAccessEvents';
import { MODES } from '../PermissionModeChip';
import { PanelSection } from './PanelSection';
import { Switch } from './Switch';
import type { LocalModelStatus, PermissionMode, TunnelStatus } from '../../types';
import type { ScopeEntry, ScopeSource } from '../../types/sessionPanel';
import type { MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const SOURCE_LABEL: Record<ScopeSource, MessageKey> = {
    added: 'env.source.added',
    gaia_documents: 'env.source.gaia_documents',
    scratch: 'env.source.scratch',
    document: 'env.source.document',
    working_folder: 'env.source.working_folder',
    approved: 'env.source.approved',
    allowed_paths: 'env.source.allowed_paths',
};

const FILES_SHOWN = 3;

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

export function EnvironmentSection({ sessionId, busy }: { sessionId: string; busy: boolean }) {
    const t = useT();
    const { env } = useSessionEnvironment(sessionId);
    const summary = env && !env.internet ? t('env.offline') : null;
    return (
        <PanelSection id="environment" title={t('env.title')} summary={summary}>
            <RunsOn sessionId={sessionId} busy={busy} />
            <Folders sessionId={sessionId} />
            <Internet sessionId={sessionId} />
            <PhoneAccess />
            <Permission sessionId={sessionId} />
        </PanelSection>
    );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="sp-env-row">
            <div className="sp-env-label">{label}</div>
            <div className="sp-env-value">{children}</div>
        </div>
    );
}

/** Lemonade ids compare case-insensitively, with or without the ``user.`` prefix. */
function sameModel(a: string, b: string): boolean {
    const norm = (id: string) => id.toLowerCase().replace(/^user\./, '');
    return norm(a) === norm(b);
}

/** Where the model answering this chat runs, from the same data as the model status popover. */
function RunsOn({ sessionId, busy }: { sessionId: string; busy: boolean }) {
    const t = useT();
    const place = useInferencePlace();
    const active = useModelStore((s) => s.active);
    const chatModel = useChatStore((s) => selectChipModel(active, s.sessions, sessionId));
    const [status, setStatus] = useState<LocalModelStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    const remote = place?.remote === true;

    const load = useCallback(() => {
        if (remote) return;
        Promise.resolve()
            .then(() => api.getLocalModelStatus())
            .then((s) => {
                setStatus(expectShape(s, (r) => typeof r.state === 'string', 'model status'));
                setError(null);
            })
            .catch((err) => {
                log.ui.warn('Could not read where the local model runs', err);
                setError(errorText(err));
            });
    }, [remote]);
    useEffect(load, [load]);
    useTurnEnd(busy, load);

    if (remote) {
        return (
            <Row label={t('env.runsOn')}>
                <span className="sp-place is-remote"><Cloud size={13} aria-hidden="true" /> {t('env.cloudPlace', { name: place?.label ?? '' })}</span>
                {place?.description && <span className="sp-meta">{place.description}</span>}
            </Row>
        );
    }
    if (!place) {
        return <Row label={t('env.runsOn')}><span className="sp-meta" title={UNKNOWN_PLACE_TITLE}>{t('env.notKnownYet')}</span></Row>;
    }
    // The server may have another chat's model loaded; only describe it when it is this chat's.
    const other = !!(status?.model && chatModel && !sameModel(status.model, chatModel));
    const memory = other ? null : status?.memory;
    return (
        <Row label={t('env.runsOn')}>
            <span className="sp-place"><Monitor size={13} aria-hidden="true" /> {t('env.thisPc')}{!other && status?.placement?.label ? ` · ${status.placement.label}` : ''}</span>
            {other
                ? <span className="sp-meta">{t('env.notLoaded', { model: displayModelName(chatModel!) })}</span>
                : status?.display_name && <span className="sp-meta" dir="ltr">{status.display_name}{status.state !== 'loaded' ? ` · ${status.state}` : ''}</span>}
            {memory && (
                <span className="sp-meta">
                    {memory.model_gb != null ? t('env.inUse', { gb: memory.model_gb.toFixed(1) }) : t('env.inUseUnknown')}
                    {memory.free_gb != null ? ` · ${t('env.free', { gb: memory.free_gb.toFixed(1) })}` : ''}
                </span>
            )}
            {(error || status?.error) && <span className="sp-error">{error ?? status?.error}</span>}
        </Row>
    );
}

function Folders({ sessionId }: { sessionId: string }) {
    const t = useT();
    const { env, error: loadError, change } = useSessionEnvironment(sessionId);
    // Only the flagship's own default scope (your user folder) is known here.
    const flagship = useChatStore((s) => s.sessions.find((x) => x.id === sessionId)?.agent_type === 'gaia');
    const [error, setError] = useState<string | null>(null);
    const [busyPath, setBusyPath] = useState<string | null>(null);
    const [showAllFiles, setShowAllFiles] = useState(false);

    const run = async (path: string, op: () => Promise<unknown>) => {
        setBusyPath(path);
        setError(null);
        try {
            await op();
        } catch (err) {
            setError(errorText(err));
        } finally {
            setBusyPath(null);
        }
    };

    const remove = (entry: ScopeEntry) =>
        run(entry.path, () => change(() => api.removeEnvironmentFolder(sessionId, entry.path)));
    const add = (path: string) => {
        void run(path, () => change(() => api.addEnvironmentFolder(sessionId, path)));
    };
    const { open: pickFolder, picker, error: pickError } = useFolderPicker(add);

    const folders = env?.folders.filter((f) => f.kind === 'folder') ?? [];
    const files = env?.folders.filter((f) => f.kind === 'file') ?? [];
    const shownFiles = showAllFiles ? files : files.slice(0, FILES_SHOWN);

    const locked = env ? !env.enforced : true;
    const item = (entry: ScopeEntry) => (
        <li key={entry.path} className="sp-scope-item">
            {entry.kind === 'folder'
                ? <Folder size={13} className="sp-item-icon" aria-hidden="true" />
                : <File size={13} className="sp-item-icon" aria-hidden="true" />}
            <div className="sp-item-text">
                <span className="sp-item-name" dir="auto" title={entry.path}>{baseName(entry.path)}</span>
                <span className="sp-meta" title={entry.path}>{t(SOURCE_LABEL[entry.source])} · <span dir="ltr">{entry.path}</span></span>
            </div>
            {entry.removable && !locked && (
                <button
                    type="button"
                    className="btn-icon-sm sp-item-remove"
                    onClick={() => void remove(entry)}
                    disabled={busyPath !== null}
                    aria-label={t('env.remove', { path: entry.path })}
                    title={t('env.removeShort')}
                >
                    <X size={12} />
                </button>
            )}
        </li>
    );

    return (
        <Row label={env && !env.enforced ? t('env.reach') : t('env.readWrite')}>
            {!env && !loadError && <span className="sp-meta">{t('sp.checking')}</span>}
            {env && !env.enforced && (
                <span className="sp-meta">
                    {t(flagship ? 'env.flagshipScope' : 'env.agentScope')}
                </span>
            )}
            {loadError && <span className="sp-error">{t('env.foldersError', { error: loadError })}</span>}
            {env && (
                <>
                    {env.folders.length === 0 && <span className="sp-meta">{t('env.nothing')}</span>}
                    <ul className="sp-list">
                        {folders.map(item)}
                        {shownFiles.map(item)}
                    </ul>
                    {files.length > FILES_SHOWN && (
                        <button type="button" className="link-btn sp-more" onClick={() => setShowAllFiles((v) => !v)}>
                            {showAllFiles ? t('env.fewerFiles') : t('env.allFiles', { n: files.length })}
                        </button>
                    )}
                    {!env.enforced && (
                        <span className="sp-meta">
                            {t('env.ownProcess')}
                        </span>
                    )}
                    {env.removed.length > 0 && (
                        <span className="sp-meta">
                            {t('env.removed', { names: env.removed.map(baseName).join(', ') })}
                        </span>
                    )}
                    {!locked && (
                        <button type="button" className="sp-action" onClick={() => pickFolder()} disabled={busyPath !== null}>
                            <FolderPlus size={12} aria-hidden="true" /> {t('env.addFolder')}
                        </button>
                    )}
                </>
            )}
            {(error ?? pickError) && <span className="sp-error" role="alert">{error ?? pickError}</span>}
            {picker}
        </Row>
    );
}

function Internet({ sessionId }: { sessionId: string }) {
    const t = useT();
    const { env, change, refresh } = useSessionEnvironment(sessionId);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    if (!env) return null;
    const toggle = async (on: boolean) => {
        setSaving(true);
        setError(null);
        try {
            await change(() => api.setInternet(sessionId, on));
        } catch (err) {
            setError(errorText(err));
            // Saved even when the running turn couldn't be told, so show what was saved.
            void refresh();
        } finally {
            setSaving(false);
        }
    };
    if (!env.internet_enforced) {
        return (
            <Row label={t('env.internet')}>
                <span className="sp-state-text">{env.internet ? t('env.on') : t('env.off')}</span>
            </Row>
        );
    }
    return (
        <Row label={t('env.internet')}>
            <Switch
                checked={env.internet}
                onChange={(on) => void toggle(on)}
                disabled={saving}
                label={t('env.internetFor')}
                text={env.internet ? t('env.on') : t('env.offBlocked')}
            />
            {error && <span className="sp-error" role="alert">{error}</span>}
        </Row>
    );
}

function PhoneAccess() {
    const t = useT();
    const [status, setStatus] = useState<TunnelStatus | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(() => {
        Promise.resolve()
            .then(() => api.getTunnelStatus())
            .then((s) => {
                setStatus(expectShape(s, (r) => typeof r.active === 'boolean', 'phone access status'));
                setError(null);
            })
            .catch((err) => setError(errorText(err)));
    }, []);
    useEffect(load, [load]);
    useMobileAccessState(() => { setPending(false); load(); });

    const active = status?.active === true;
    return (
        <Row label={t('env.phone')}>
            <Switch
                checked={active}
                onChange={(on) => { setPending(true); requestMobileAccess(on); }}
                disabled={pending}
                label={t('env.phone')}
                text={pending ? (active ? t('env.stopping') : t('env.starting')) : active ? t('env.on') : t('env.off')}
            />
            {active && (
                <span className="sp-meta">
                    <Smartphone size={12} aria-hidden="true" />{' '}
                    {status?.device ? t('env.phoneConnected', { name: status.device.name }) : t('env.noPhone')}
                </span>
            )}
            {active && (
                <button type="button" className="sp-action" onClick={() => requestMobileAccess(true)}>
                    {t('env.qr')}
                </button>
            )}
            {(error || status?.error) && <span className="sp-error">{error ?? status?.error}</span>}
        </Row>
    );
}

/** The chat's permission mode, mirrored with the composer's chip. */
function Permission({ sessionId }: { sessionId: string }) {
    const t = useT();
    const [mode, setMode] = useState<PermissionMode | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let live = true;
        Promise.resolve()
            .then(() => api.getPermissions(sessionId))
            .then((p) => {
                if (live) setMode(expectShape(p, (r) => typeof r.mode === 'string', 'permission mode').mode);
            })
            .catch((err) => { if (live) setError(errorText(err)); });
        return () => { live = false; };
    }, [sessionId]);
    usePermissionModeEvents(sessionId, setMode);

    const save = async (next: PermissionMode) => {
        setConfirming(false);
        setError(null);
        try {
            const saved = (await api.setPermissionMode(sessionId, next)).mode;
            setMode(saved);
            announcePermissionMode(sessionId, saved);
        } catch (err) {
            setError(errorText(err));
        }
    };

    const note = MODES.find((m) => m.mode === mode)?.note;
    return (
        <Row label={t('env.permission')}>
            <div className="sp-segmented" role="radiogroup" aria-label={t('env.permission')}>
                {MODES.map((m) => (
                    <button
                        key={m.mode}
                        type="button"
                        role="radio"
                        aria-checked={mode === m.mode}
                        className={mode === m.mode ? 'is-selected' : undefined}
                        // Full access only turns on from "Turn it on", so a double-click can't confirm it.
                        onClick={() => (m.mode === 'full_access' ? setConfirming(true) : void save(m.mode))}
                    >
                        {m.mode === 'full_access'
                            ? <ShieldAlert size={12} aria-hidden="true" />
                            : <ShieldCheck size={12} aria-hidden="true" />}
                        {m.mode === 'ask' ? t('env.ask') : t(m.label)}
                    </button>
                ))}
            </div>
            {note && <span className="sp-meta">{t(note)}</span>}
            {confirming && (
                <span className="sp-confirm" role="alert">
                    {t('env.fullAccessWarn')}
                    <button type="button" className="sp-action is-warning" onClick={() => void save('full_access')}>{t('env.turnOn')}</button>
                    <button type="button" className="sp-action" onClick={() => setConfirming(false)}>{t('sp.cancel')}</button>
                </span>
            )}
            {error && <span className="sp-error" role="alert">{error}</span>}
        </Row>
    );
}
