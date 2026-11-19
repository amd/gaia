// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Environment (#5276): where this chat runs and what it may touch. Every
 * switch here changes what the agent is allowed to do on its next step — the
 * backend enforces it in the agent's path checks and tool policy.
 */

import { RadioGroup } from '../RadioGroup';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import {
    ChevronRight, Cloud, Cpu, File, Folder, FolderPlus, Gauge, GitBranch, Globe, Info, Mail, Monitor, ShieldAlert, ShieldCheck,
    Smartphone, X,
} from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { requestErrorText } from '../../utils/requestErrors';
import { log } from '../../utils/logger';
import { useInferencePlace, useModelStore, selectChipModel, displayModelName, UNKNOWN_PLACE_TITLE } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import { useSessionPanelStore } from '../../stores/sessionPanelStore';
import { useNotificationStore, selectSessionPermissionPrompt } from '../../stores/notificationStore';
import { useFolderPicker } from '../../hooks/useFolderPicker';
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { announcePermissionMode, usePermissionModeEvents } from '../../utils/permissionModeEvents';
import { requestMobileAccess, useMobileAccessState } from '../../utils/mobileAccessEvents';
import { MODES } from '../PermissionModeChip';
import { ctxLabel } from '../modelMeta';
import { LocalStatusError } from '../LocalStatusError';
import { PanelSection } from './PanelSection';
import { Switch } from './Switch';
import { useAppear } from './appear';
import type { ContextUsage, LocalModelStatus, PermissionMode, TunnelStatus } from '../../types';
import type { ScopeEntry, ScopeSource, SessionGit } from '../../types/sessionPanel';
import type { MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const MODEL_STATE: Record<Exclude<LocalModelStatus['state'], 'loaded'>, MessageKey> = {
    warming: 'modelStatus.state.warming',
    idle: 'modelStatus.state.idle',
    stopped: 'modelStatus.state.stopped',
};

/** A warming model settles without a turn ending, so it is re-read until it does. */
const WARMING_POLL_MS = 3000;

const SOURCE_LABEL: Record<ScopeSource, MessageKey> = {
    added: 'env.source.added',
    gaia_documents: 'env.source.gaia_documents',
    scratch: 'env.source.scratch',
    document: 'env.source.document',
    working_folder: 'env.source.working_folder',
    approved: 'env.source.approved',
    allowed_paths: 'env.source.allowed_paths',
};

/** Folders GAIA keeps for itself, the same in every chat. */
const OWN_SOURCES: ReadonlySet<ScopeSource> = new Set<ScopeSource>(['gaia_documents', 'scratch']);

const FILES_SHOWN = 3;
const ICON = 14;

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

interface EnvironmentSectionProps {
    sessionId: string;
    busy: boolean;
    /** The chat's measured context use; its row shows once there is one. */
    usage?: ContextUsage | null;
    /** The chat's documents have a section of their own, so leave them out of the folders. */
    hideDocuments?: boolean;
}

export function EnvironmentSection({ sessionId, busy, usage, hideDocuments = false }: EnvironmentSectionProps) {
    const t = useT();
    const { env, refresh } = useSessionEnvironment(sessionId);
    // A turn can add a document or folder to what the chat reaches.
    useTurnEnd(busy, () => { void refresh(); });
    const summary = env && !env.internet ? t('env.offline') : null;
    return (
        <PanelSection id="environment" title={t('env.title')} summary={summary}>
            <div className="sp-rows">
                <RunsOn sessionId={sessionId} busy={busy} />
                <ContextRow usage={usage ?? null} />
                <Mailbox sessionId={sessionId} />
                <Project sessionId={sessionId} busy={busy} />
                <Folders sessionId={sessionId} hideDocuments={hideDocuments} />
                <Permission sessionId={sessionId} />
                <Internet sessionId={sessionId} />
                <PhoneAccess />
            </div>
        </PanelSection>
    );
}

/** How full the context window is, once the chat has used any: the one number worth watching as a chat grows. */
function ContextRow({ usage }: { usage: ContextUsage | null }) {
    const t = useT();
    const reveal = useSessionPanelStore((s) => s.reveal);
    const appear = useAppear();
    if (!usage || usage.window <= 0) return null;
    const pct = Math.min(100, Math.round((usage.used / usage.window) * 100));
    const level = pct >= 80 ? ' is-high' : '';
    return (
        <div className={`sp-row${appear}`}>
            <button type="button" className="sp-row-line sp-row-button" onClick={() => reveal('context')} title={t('env.contextTitle')}>
                <span className="sp-row-icon" aria-hidden="true"><Gauge size={ICON} /></span>
                <span className="sp-row-label">{t('env.context')}</span>
                <span className="sp-row-end">
                    <bdi className="sp-quiet" dir="ltr">{pct}%</bdi>
                    <span className={`sp-meter${level}`} aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
                </span>
            </button>
        </div>
    );
}

const MAILBOX: Record<string, MessageKey> = { google: 'env.mailbox.google', microsoft: 'env.mailbox.microsoft' };

/** The mailbox an email chat reads, since that is what it can reach. */
function Mailbox({ sessionId }: { sessionId: string }) {
    const t = useT();
    const provider = useChatStore((s) => {
        const session = s.sessions.find((x) => x.id === sessionId);
        return session?.agent_type === 'email' ? session.mail_provider ?? null : null;
    });
    const key = provider ? MAILBOX[provider] : undefined;
    if (!key) return null;
    return <Row icon={<Mail size={ICON} />} label={t(key)} title={t('env.mailboxTitle')} />;
}

/** A repository is named by its folder, whichever way its path's slashes lean. */
const repoName = baseName;

/** The git repository and branch holding the chat's changes, once it has changed files. */
function Project({ sessionId, busy }: { sessionId: string; busy: boolean }) {
    const t = useT();
    const changed = useSessionPanelStore((s) => s.signals[sessionId]?.changedFiles ?? 0);
    const [git, setGit] = useState<SessionGit | null>(null);
    const appear = useAppear();

    // Reads can overlap (a turn ending while one is out); only the latest may set the row.
    const latest = useRef(0);
    const load = useCallback(() => {
        const read = ++latest.current;
        if (changed === 0) {
            setGit(null);
            return;
        }
        Promise.resolve()
            .then(() => api.getSessionGit(sessionId))
            .then((res) => {
                if (read === latest.current) setGit(expectShape(res, (r) => Array.isArray(r.repos), 'git state'));
            })
            // The Changes section reports a failed read; this row just isn't shown.
            .catch((err) => {
                log.ui.warn(`Could not read the git state of session=${sessionId}`, err);
                if (read === latest.current) setGit(null);
            });
    }, [changed, sessionId]);
    useEffect(() => {
        load();
        // Leaving (or a new chat) retires any read still out.
        return () => { latest.current += 1; };
    }, [load]);
    useTurnEnd(busy, load);

    if (!git || git.repos.length === 0) return null;
    return (
        <>
            {git.repos.map((repo) => (
                <div key={repo.root} className={`sp-row${appear}`}>
                    <div className="sp-row-line" title={t('env.branchTitle')}>
                        <span className="sp-row-icon" aria-hidden="true"><GitBranch size={ICON} /></span>
                        <span className="sp-row-label" dir="ltr">
                            {repoName(repo.root)} <span className="sp-quiet">{repo.branch}</span>
                        </span>
                        {repo.uncommitted.length > 0 && (
                            <span className="sp-row-end sp-quiet">
                                {t.plural('env.uncommitted', repo.uncommitted.length, { n: repo.uncommitted.length })}
                            </span>
                        )}
                    </div>
                </div>
            ))}
        </>
    );
}

interface RowProps {
    icon: ReactNode;
    /** What the row is: a name or a short fact, on one line. */
    label: ReactNode;
    /** Shown on hover over the label. */
    title?: string;
    /** The control or a quiet detail, at the end of the line. */
    children?: ReactNode;
    /** Help that opens from an info button, so it never sits in the way. */
    info?: { text: string; label: string };
    /** Anything that needs the full width: a warning, a confirmation, an error. */
    below?: ReactNode;
    className?: string;
}

/** One line: an icon, what it is, and its control or detail at the end. */
function Row({ icon, label, title, children, info, below, className }: RowProps) {
    const [showInfo, setShowInfo] = useState(false);
    return (
        <div className={`sp-row${className ? ` ${className}` : ''}`}>
            <div className="sp-row-line">
                <span className="sp-row-icon" aria-hidden="true">{icon}</span>
                <span className="sp-row-label" title={title}>{label}</span>
                {info && (
                    <button
                        type="button"
                        className="btn-icon-sm sp-info"
                        onClick={() => setShowInfo((v) => !v)}
                        aria-expanded={showInfo}
                        aria-label={info.label}
                        title={info.label}
                    >
                        <Info size={13} />
                    </button>
                )}
                {children != null && <span className="sp-row-end">{children}</span>}
            </div>
            {info && showInfo && <span className="sp-note">{info.text}</span>}
            {below}
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
                setError(requestErrorText(err));
            });
    }, [remote]);
    useEffect(load, [load]);
    useTurnEnd(busy, load);
    const warming = status?.state === 'warming';
    useEffect(() => {
        if (!warming) return undefined;
        const timer = setInterval(load, WARMING_POLL_MS);
        return () => clearInterval(timer);
    }, [warming, load]);

    if (remote) {
        return (
            <>
                <Row
                    className="is-remote"
                    icon={<Cloud size={ICON} />}
                    label={t('env.cloudPlace', { name: place?.label ?? '' })}
                    info={place?.description ? { text: place.description, label: t('env.aboutCloud') } : undefined}
                />
                {chatModel && (
                    <Row icon={<Cpu size={ICON} />} label={<span dir="ltr">{displayModelName(chatModel)}</span>} title={chatModel} />
                )}
            </>
        );
    }
    if (!place) {
        return <Row icon={<Monitor size={ICON} />} label={<span className="sp-quiet">{t('env.notKnownYet')}</span>} title={UNKNOWN_PLACE_TITLE} />;
    }
    // The server may have another chat's model loaded; only describe it when it is this chat's.
    const other = !!(status?.model && chatModel && !sameModel(status.model, chatModel));
    const memory = other ? null : status?.memory;
    // With nothing loaded the server names no model, so name this chat's.
    const modelName = status?.display_name
        ?? (status && status.state !== 'loaded' && chatModel ? displayModelName(chatModel) : null);
    const inUse = memory?.model_gb != null
        ? t('env.inUse', { gb: memory.model_gb.toFixed(1) })
        : status?.state === 'loaded' || status?.state === 'warming' ? t('env.inUseUnknown') : null;
    const free = memory?.free_gb != null ? t('env.free', { gb: memory.free_gb.toFixed(1) }) : null;
    const memoryText = memory ? [inUse, free].filter(Boolean).join(' · ') || undefined : undefined;
    return (
        <>
            <Row
                icon={<Monitor size={ICON} />}
                label={`${t('env.thisPc')}${!other && status?.placement?.label ? ` · ${status.placement.label}` : ''}`}
                below={error
                    ? <span className="sp-error">{error}</span>
                    : status?.error && <span className="sp-error"><LocalStatusError status={status} /></span>}
            />
            {other && (
                <Row icon={<Cpu size={ICON} />} label={<span className="sp-quiet">{t('env.notLoaded', { model: displayModelName(chatModel!) })}</span>} />
            )}
            {!other && status && modelName && (
                <Row
                    icon={<Cpu size={ICON} />}
                    label={(
                        <>
                            <bdi dir="ltr">{modelName}</bdi>
                            {status.state !== 'loaded' ? ` · ${t(MODEL_STATE[status.state])}` : ''}
                        </>
                    )}
                    title={memoryText}
                >
                    {status.ctx_size != null && (
                        <span className="sp-quiet" title={t('modelStatus.context')}>
                            {t('settings.ctx.tokens', { size: ctxLabel(status.ctx_size) })}
                        </span>
                    )}
                </Row>
            )}
        </>
    );
}

function Folders({ sessionId, hideDocuments }: { sessionId: string; hideDocuments: boolean }) {
    const t = useT();
    const { env, error: loadError, change } = useSessionEnvironment(sessionId);
    // Only the flagship's own default scope (your user folder) is known here.
    const flagship = useChatStore((s) => s.sessions.find((x) => x.id === sessionId)?.agent_type === 'gaia');
    const [error, setError] = useState<string | null>(null);
    const [busyPath, setBusyPath] = useState<string | null>(null);
    const [showAllFiles, setShowAllFiles] = useState(false);
    const [showAbout, setShowAbout] = useState(false);
    const [showOwn, setShowOwn] = useState(false);

    const run = async (path: string, op: () => Promise<unknown>) => {
        setBusyPath(path);
        setError(null);
        try {
            await op();
        } catch (err) {
            setError(requestErrorText(err));
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

    const listed = (env?.folders ?? []).filter((f) => !(hideDocuments && f.kind === 'file' && f.source === 'document'));
    // GAIA's own folders are there in every chat; they wait behind one line.
    const own = listed.filter((f) => OWN_SOURCES.has(f.source));
    const folders = listed.filter((f) => f.kind === 'folder' && !OWN_SOURCES.has(f.source));
    const files = listed.filter((f) => f.kind === 'file' && !OWN_SOURCES.has(f.source));
    const shownFiles = showAllFiles ? files : files.slice(0, FILES_SHOWN);

    const locked = env ? !env.enforced : true;
    const item = (entry: ScopeEntry) => (
        <li key={entry.path} className="sp-scope-item sp-row-line">
            <span className="sp-row-icon" aria-hidden="true">
                {entry.kind === 'folder' ? <Folder size={ICON} /> : <File size={ICON} />}
            </span>
            <span className="sp-item-name" dir="ltr" title={entry.path}>{entry.name || baseName(entry.path)}</span>
            {/* A folder's path says which one it is; a file's shows on hover. Where each came from is on hover too. */}
            {entry.kind === 'folder'
                ? <span className="sp-item-path sp-quiet" dir="ltr" title={t(SOURCE_LABEL[entry.source])}>{entry.path}</span>
                : <span className="sp-item-source sp-quiet" title={entry.path}>{t(SOURCE_LABEL[entry.source])}</span>}
            {entry.removable && !locked && (
                <button
                    type="button"
                    className="btn-icon-sm sp-info sp-item-remove"
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

    // A chat whose agent keeps its own scope lists where it starts, which needs explaining.
    const about = env && !env.enforced ? t(flagship ? 'env.flagshipScope' : 'env.agentScope') : null;
    const canAdd = !!env && !locked;
    return (
        <div className="sp-row">
            {!env && !loadError && <span className="sp-quiet">{t('sp.checking')}</span>}
            {loadError && <span className="sp-error">{t('env.foldersError', { error: loadError })}</span>}
            {env && (
                <>
                    {folders.length + shownFiles.length > 0 && (
                        <ul className="sp-list" aria-label={env.enforced ? t('env.readWrite') : t('env.reach')}>
                            {folders.map(item)}
                            {shownFiles.map(item)}
                        </ul>
                    )}
                    {files.length > FILES_SHOWN && (
                        <button type="button" className="link-btn sp-more" onClick={() => setShowAllFiles((v) => !v)}>
                            {showAllFiles ? t('env.fewerFiles') : t('env.allFiles', { n: files.length })}
                        </button>
                    )}
                    {(canAdd || about || env.folders.length === 0) && (
                        <div className="sp-row-line">
                            {canAdd ? (
                                <button type="button" className="sp-row-button" onClick={() => pickFolder()} disabled={busyPath !== null}>
                                    <span className="sp-row-icon" aria-hidden="true"><FolderPlus size={ICON} /></span>
                                    {t('env.addFolder')}
                                </button>
                            ) : (
                                <span className="sp-row-label sp-quiet">{env.folders.length === 0 ? t('env.nothing') : t('env.reach')}</span>
                            )}
                            {about && (
                                <button
                                    type="button"
                                    className="btn-icon-sm sp-info"
                                    onClick={() => setShowAbout((v) => !v)}
                                    aria-expanded={showAbout}
                                    aria-label={t('env.aboutFolders')}
                                    title={t('env.aboutFolders')}
                                >
                                    <Info size={13} />
                                </button>
                            )}
                        </div>
                    )}
                    {about && showAbout && <span className="sp-note">{about}</span>}
                    {own.length > 0 && (
                        <button
                            type="button"
                            className="sp-row-line sp-row-button"
                            onClick={() => setShowOwn((v) => !v)}
                            aria-expanded={showOwn}
                            title={t('env.ownFoldersTitle')}
                        >
                            <span className="sp-row-icon" aria-hidden="true">
                                <ChevronRight size={ICON} className={`sp-chevron${showOwn ? ' is-open' : ''}`} />
                            </span>
                            <span className="sp-row-label">{t('env.ownFolders')}</span>
                            <bdi className="sp-row-end sp-quiet" dir="ltr">{own.length}</bdi>
                        </button>
                    )}
                    {showOwn && own.length > 0 && (
                        <ul className="sp-list sp-row-sub" aria-label={t('env.ownFolders')}>{own.map(item)}</ul>
                    )}
                    {env.removed.length > 0 && (
                        <span className="sp-quiet">
                            {t('env.removed', { names: env.removed.map(baseName).join(', ') })}
                        </span>
                    )}
                </>
            )}
            {(error ?? pickError) && <span className="sp-error" role="alert">{error ?? pickError}</span>}
            {picker}
        </div>
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
            setError(requestErrorText(err));
            // Saved even when the running turn couldn't be told, so show what was saved.
            void refresh();
        } finally {
            setSaving(false);
        }
    };
    const icon = <Globe size={ICON} />;
    if (!env.internet_enforced) {
        return (
            <Row icon={icon} label={t('env.internet')}>
                <span className="sp-quiet">{env.internet ? t('env.on') : t('env.off')}</span>
            </Row>
        );
    }
    return (
        <Row
            icon={icon}
            label={t('env.internet')}
            title={env.internet ? undefined : t('env.blocked')}
            below={error && <span className="sp-error" role="alert">{error}</span>}
        >
            <Switch
                checked={env.internet}
                onChange={(on) => void toggle(on)}
                disabled={saving}
                label={t('env.internetFor')}
                text={env.internet ? t('env.on') : t('env.off')}
            />
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
            .catch((err) => setError(requestErrorText(err)));
    }, []);
    useEffect(load, [load]);
    useMobileAccessState(() => { setPending(false); load(); });

    const active = status?.active === true;
    return (
        <Row
            icon={<Smartphone size={ICON} />}
            label={t('env.phone')}
            below={(
                <>
                    {active && (
                        <span className="sp-row-line sp-row-sub">
                            <span className="sp-row-label sp-quiet">
                                {status?.device ? t('env.phoneConnected', { name: status.device.name }) : t('env.noPhone')}
                            </span>
                            <button type="button" className="sp-action" onClick={() => requestMobileAccess(true)}>
                                {t('env.qr')}
                            </button>
                        </span>
                    )}
                    {/* A failed start is explained in the Mobile access dialog; it isn't this switch's error. */}
                    {(error || (active && status?.error)) && <span className="sp-error">{error ?? status?.error}</span>}
                </>
            )}
        >
            <Switch
                checked={active}
                onChange={(on) => { setPending(true); requestMobileAccess(on); }}
                disabled={pending}
                label={t('env.phone')}
                text={pending ? (active ? t('env.stopping') : t('env.starting')) : active ? t('env.on') : t('env.off')}
            />
        </Row>
    );
}

/** The chat's permission mode, mirrored with the composer's chip. */
function Permission({ sessionId }: { sessionId: string }) {
    const t = useT();
    const [mode, setMode] = useState<PermissionMode | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const promptWaiting = useNotificationStore((s) => selectSessionPermissionPrompt(sessionId)(s) !== null);

    useEffect(() => {
        let live = true;
        Promise.resolve()
            .then(() => api.getPermissions(sessionId))
            .then((p) => {
                if (live) setMode(expectShape(p, (r) => typeof r.mode === 'string', 'permission mode').mode);
            })
            .catch((err) => { if (live) setError(requestErrorText(err)); });
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
            setError(requestErrorText(err));
        }
    };

    const note = MODES.find((m) => m.mode === mode)?.note;
    return (
        <Row
            icon={mode === 'full_access' ? <ShieldAlert size={ICON} /> : <ShieldCheck size={ICON} />}
            label={t('env.permissionShort')}
            info={note ? { text: t(note), label: t('env.aboutPermission') } : undefined}
            below={(
                <>
                    {mode === 'full_access' && promptWaiting && <span className="sp-quiet" role="status">{t('perm.fullPendingNote')}</span>}
                    {confirming && (
                        <span className="sp-confirm" role="alert">
                            {t('env.fullAccessWarn')}
                            <span className="sp-actions-row">
                                <button type="button" className="sp-action is-warning" onClick={() => void save('full_access')}>{t('env.turnOn')}</button>
                                <button type="button" className="sp-action" onClick={() => setConfirming(false)}>{t('sp.cancel')}</button>
                            </span>
                        </span>
                    )}
                    {error && <span className="sp-error" role="alert">{error}</span>}
                </>
            )}
        >
            <RadioGroup className="sp-segmented" aria-label={t('env.permission')}>
                {MODES.map((m) => (
                    <button
                        key={m.mode}
                        type="button"
                        role="radio"
                        aria-checked={mode === m.mode}
                        className={`${mode === m.mode ? 'is-selected' : ''}${m.mode === 'full_access' ? ' is-risky' : ''}`}
                        // Full access only turns on from "Turn it on", so a double-click can't confirm it.
                        onClick={() => (m.mode === 'full_access' ? setConfirming(true) : void save(m.mode))}
                    >
                        {m.mode === 'ask' ? t('env.ask') : t(m.label)}
                    </button>
                ))}
            </RadioGroup>
        </Row>
    );
}
