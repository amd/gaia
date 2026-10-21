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
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { announcePermissionMode, usePermissionModeEvents } from '../../utils/permissionModeEvents';
import { requestMobileAccess, useMobileAccessState } from '../../utils/mobileAccessEvents';
import { MODES } from '../PermissionModeChip';
import { PanelSection } from './PanelSection';
import { FolderPicker } from './FolderPicker';
import { Switch } from './Switch';
import type { LocalModelStatus, PermissionMode, TunnelStatus } from '../../types';
import type { ScopeEntry, ScopeSource } from '../../types/sessionPanel';

const SOURCE_LABEL: Record<ScopeSource, string> = {
    added: 'Added by you',
    gaia_documents: "GAIA's documents folder",
    scratch: "GAIA's scratch folder",
    document: 'Document in this chat',
    working_folder: 'Folder GAIA started in',
    approved: 'Approved during this chat',
    allowed_paths: 'Set by GAIA_ALLOWED_PATHS',
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
    const { env } = useSessionEnvironment(sessionId);
    const summary = env && !env.internet ? 'Offline' : null;
    return (
        <PanelSection id="environment" title="Environment" summary={summary}>
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

/** Where the model answering this chat runs, from the same data as the model status popover. */
/** Lemonade ids compare case-insensitively, with or without the ``user.`` prefix. */
function sameModel(a: string, b: string): boolean {
    const norm = (id: string) => id.toLowerCase().replace(/^user\./, '');
    return norm(a) === norm(b);
}

function RunsOn({ sessionId, busy }: { sessionId: string; busy: boolean }) {
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
            <Row label="Runs on">
                <span className="sp-place is-remote"><Cloud size={13} aria-hidden="true" /> {place?.label} (cloud)</span>
                {place?.description && <span className="sp-meta">{place.description}</span>}
            </Row>
        );
    }
    if (!place) {
        return <Row label="Runs on"><span className="sp-meta" title={UNKNOWN_PLACE_TITLE}>Not known yet</span></Row>;
    }
    // The server may have another chat's model loaded; only describe it when it is this chat's.
    const other = !!(status?.model && chatModel && !sameModel(status.model, chatModel));
    const memory = other ? null : status?.memory;
    return (
        <Row label="Runs on">
            <span className="sp-place"><Monitor size={13} aria-hidden="true" /> This PC{!other && status?.placement?.label ? ` · ${status.placement.label}` : ''}</span>
            {other
                ? <span className="sp-meta">{displayModelName(chatModel!)} · not loaded right now</span>
                : status?.display_name && <span className="sp-meta">{status.display_name}{status.state !== 'loaded' ? ` · ${status.state}` : ''}</span>}
            {memory && (
                <span className="sp-meta">
                    {memory.model_gb != null ? `${memory.model_gb.toFixed(1)} GB in use` : 'Memory in use unknown'}
                    {memory.free_gb != null ? ` · ${memory.free_gb.toFixed(1)} GB free` : ''}
                </span>
            )}
            {(error || status?.error) && <span className="sp-error">{error ?? status?.error}</span>}
        </Row>
    );
}

function Folders({ sessionId }: { sessionId: string }) {
    const { env, error: loadError, change } = useSessionEnvironment(sessionId);
    const [picking, setPicking] = useState(false);
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
        setPicking(false);
        void run(path, () => change(() => api.addEnvironmentFolder(sessionId, path)));
    };

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
                <span className="sp-item-name" title={entry.path}>{baseName(entry.path)}</span>
                <span className="sp-meta" title={entry.path}>{SOURCE_LABEL[entry.source]} · {entry.path}</span>
            </div>
            {entry.removable && !locked && (
                <button
                    type="button"
                    className="btn-icon-sm sp-item-remove"
                    onClick={() => void remove(entry)}
                    disabled={busyPath !== null}
                    aria-label={`Remove ${entry.path} from this chat`}
                    title="Remove from this chat"
                >
                    <X size={12} />
                </button>
            )}
        </li>
    );

    return (
        <Row label={env && !env.enforced ? 'Files GAIA can reach' : 'Can read and write'}>
            {!env && !loadError && <span className="sp-meta">Checking…</span>}
            {env && !env.enforced && (
                <span className="sp-meta">
                    These folders are where GAIA starts, not a limit: it can also reach the rest of
                    your user folder. In Ask mode it asks before it changes anything.
                </span>
            )}
            {loadError && <span className="sp-error">Couldn't read this chat's folders: {loadError}</span>}
            {env && (
                <>
                    {env.folders.length === 0 && <span className="sp-meta">Nothing yet. GAIA can't read or write any folder in this chat.</span>}
                    <ul className="sp-list">
                        {folders.map(item)}
                        {shownFiles.map(item)}
                    </ul>
                    {files.length > FILES_SHOWN && (
                        <button type="button" className="link-btn sp-more" onClick={() => setShowAllFiles((v) => !v)}>
                            {showAllFiles ? 'Show fewer files' : `Show all ${files.length} files`}
                        </button>
                    )}
                    {!env.enforced && (
                        <span className="sp-meta">
                            This chat's agent runs as its own process, so these settings can't be changed from here.
                        </span>
                    )}
                    {env.removed.length > 0 && (
                        <span className="sp-meta">
                            Removed: {env.removed.map(baseName).join(', ')}. GAIA is refused there until you add it back.
                        </span>
                    )}
                    {!locked && (
                        <button type="button" className="sp-action" onClick={() => setPicking(true)} disabled={busyPath !== null}>
                            <FolderPlus size={12} aria-hidden="true" /> Add folder
                        </button>
                    )}
                </>
            )}
            {error && <span className="sp-error" role="alert">{error}</span>}
            {picking && <FolderPicker onPick={add} onClose={() => setPicking(false)} />}
        </Row>
    );
}

function Internet({ sessionId }: { sessionId: string }) {
    const { env, change } = useSessionEnvironment(sessionId);
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
        } finally {
            setSaving(false);
        }
    };
    return (
        <Row label="Internet">
            <Switch
                checked={env.internet}
                onChange={(on) => void toggle(on)}
                disabled={saving || !env.enforced}
                label="Internet for this chat"
                text={env.internet ? 'On' : 'Off: web search and pages are blocked'}
            />
            {!env.enforced && <span className="sp-meta">Set by the agent; can't be changed from this chat.</span>}
            {error && <span className="sp-error" role="alert">{error}</span>}
        </Row>
    );
}

function PhoneAccess() {
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
        <Row label="Phone access">
            <Switch
                checked={active}
                onChange={(on) => { setPending(true); requestMobileAccess(on); }}
                disabled={pending}
                label="Phone access"
                text={pending ? (active ? 'Stopping…' : 'Starting…') : active ? 'On' : 'Off'}
            />
            {active && (
                <span className="sp-meta">
                    <Smartphone size={12} aria-hidden="true" />{' '}
                    {status?.device ? `${status.device.name} connected` : 'No phone connected yet'}
                </span>
            )}
            {active && (
                <button type="button" className="sp-action" onClick={() => requestMobileAccess(true)}>
                    Show QR code
                </button>
            )}
            {(error || status?.error) && <span className="sp-error">{error ?? status?.error}</span>}
        </Row>
    );
}

/** The chat's permission mode, mirrored with the composer's chip. */
function Permission({ sessionId }: { sessionId: string }) {
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

    const apply = async (next: PermissionMode) => {
        if (next === 'full_access' && !confirming) {
            setConfirming(true);
            return;
        }
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
        <Row label="Permission mode">
            <div className="sp-segmented" role="radiogroup" aria-label="Permission mode">
                {MODES.map((m) => (
                    <button
                        key={m.mode}
                        type="button"
                        role="radio"
                        aria-checked={mode === m.mode}
                        className={mode === m.mode ? 'is-selected' : undefined}
                        onClick={() => void apply(m.mode)}
                    >
                        {m.mode === 'full_access'
                            ? <ShieldAlert size={12} aria-hidden="true" />
                            : <ShieldCheck size={12} aria-hidden="true" />}
                        {m.mode === 'ask' ? 'Ask' : m.label}
                    </button>
                ))}
            </div>
            {note && <span className="sp-meta">{note}</span>}
            {confirming && (
                <span className="sp-confirm" role="alert">
                    Full access lets GAIA change files and run commands on this PC without asking.
                    <button type="button" className="sp-action is-warning" onClick={() => void apply('full_access')}>Turn it on</button>
                    <button type="button" className="sp-action" onClick={() => setConfirming(false)}>Cancel</button>
                </span>
            )}
            {error && <span className="sp-error" role="alert">{error}</span>}
        </Row>
    );
}
