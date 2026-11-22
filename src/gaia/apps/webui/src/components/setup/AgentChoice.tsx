// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, Download, Loader2 } from 'lucide-react';
import { RadioGroup } from '../RadioGroup';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';
import * as api from '../../services/api';
import type { AgentProgram, AgentPrograms, FlagshipAgent } from '../../types';
import { AgentDownloadRow, useAgentDownload } from './AgentDownload';
import { formatBytes } from './format';
import './setup.css';

interface Option {
    id: FlagshipAgent;
    label: MessageKey;
    more: MessageKey;
    tag: MessageKey;
}

const OPTIONS: Option[] = [
    { id: 'gaiapy', label: 'setup.agent.gaiapy', more: 'setup.agent.gaiapyMore', tag: 'setup.offer.recommended' },
    { id: 'gaiacpp', label: 'setup.agent.gaiacpp', more: 'setup.agent.gaiacppMore', tag: 'setup.agent.beta' },
];

const WHY: Record<NonNullable<AgentProgram['unavailable_code']>, MessageKey> = {
    no_release: 'setup.agent.why.noRelease',
    unreachable: 'setup.agent.why.unreachable',
    unverifiable: 'setup.agent.why.unverifiable',
};

const messageOf = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Setup's pick: saved on Continue, and downloaded first when `download` is set. */
export interface AgentPick {
    id: FlagshipAgent;
    download: boolean;
    /** The agent saved now (possibly one this version can't run), which setup carries on with if the pick can't be had. */
    current: string;
}

interface Props {
    /** In Settings: a settings card; a gaiacpp not on this PC has a Download button, and switches once that finishes. */
    inSettings?: boolean;
    disabled?: boolean;
    /** Setup: the agent picked here, held until Continue; null keeps the one in use. */
    pending?: FlagshipAgent | null;
    onPending?: (pick: AgentPick | null) => void;
}

/** Which program runs the GAIA agent: the Python one or the native C++ one. */
export function AgentChoice({ inSettings = false, disabled = false, pending = null, onPending }: Props) {
    const t = useT();
    const ids = useId();
    const titleId = `${ids}-title`;
    const moreId = `${ids}-more`;
    const offId = `${ids}-off`;
    const [listing, setListing] = useState<AgentPrograms | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [open, setOpen] = useState(false);
    const mounted = useRef(true);

    const save = useCallback(async (id: FlagshipAgent, previous: string) => {
        setSaving(true);
        setSaveError(null);
        setListing((l) => (l ? { ...l, current: id } : l));
        try {
            const updated = await api.updateSettings({ flagship_agent: id });
            if (mounted.current) setListing((l) => (l ? { ...l, current: updated.flagship_agent } : l));
        } catch (err) {
            if (!mounted.current) return;
            setSaveError(messageOf(err));
            // A 422 or 409 saved nothing; any other failure may have saved it, so read back what the server holds.
            const refused = err instanceof api.ApiError && (err.httpStatus === 422 || err.httpStatus === 409);
            const back = refused ? null : await api.getAgentPrograms().catch(() => null);
            if (mounted.current) setListing((l) => back ?? (l ? { ...l, current: previous } : l));
        } finally {
            if (mounted.current) setSaving(false);
        }
    }, []);

    const currentRef = useRef<string>('gaiapy');
    currentRef.current = listing?.current ?? 'gaiapy';
    // Switch only after a download the user started here with Download; one followed in is just shown.
    const { job, start, follow, interrupted } = useAgentDownload((startedHere) => {
        const before = currentRef.current;
        void api.getAgentPrograms()
            .then((l) => { if (mounted.current) setListing(l); }, (err: unknown) => console.warn('[AgentChoice] could not re-read the agents after the download', err))
            .then(() => { if (mounted.current && startedHere) void save('gaiacpp', before); });
    });

    useEffect(() => {
        mounted.current = true;
        api.getAgentPrograms()
            .then((l) => {
                if (!mounted.current) return;
                setListing(l);
                if (inSettings) follow(l.download);
            })
            .catch((err: unknown) => { if (mounted.current) setLoadError(messageOf(err)); });
        return () => { mounted.current = false; };
    }, [inSettings, follow]);

    const program = (id: FlagshipAgent): AgentProgram | undefined => listing?.programs.find((p) => p.id === id);
    // gaiapy is always selectable, whatever the listing says, so nobody is locked out of switching back.
    const installed = (p: AgentProgram) => p.id === 'gaiapy' || p.installed;
    const off = (p: AgentProgram | undefined) => !!p && !installed(p) && !p.can_download;
    // In Settings a gaiacpp that isn't here is had through its Download button, never by selecting it.
    const needsDownload = (p: AgentProgram | undefined) => !!p && inSettings && !installed(p) && p.can_download;
    const downloading = job?.state === 'downloading';
    const selected = pending ?? listing?.current;

    const choose = (id: FlagshipAgent) => {
        const p = program(id);
        if (!listing || !p || disabled || off(p)) return;
        // Setup saves nothing here: Continue gets the agent and saves it before setup's own test runs.
        if (!inSettings) {
            onPending?.(id === listing.current ? null : { id, download: !installed(p), current: listing.current });
            return;
        }
        if (saving || downloading || needsDownload(p)) return;
        if (id !== listing.current) void save(id, listing.current);
    };

    const cpp = program('gaiacpp');
    const why = cpp?.unavailable_code ? t(WHY[cpp.unavailable_code]) : cpp?.unavailable_reason;
    const cppNote: string | null = !cpp || installed(cpp) ? null
        : cpp.can_download ? t(inSettings ? 'setup.agent.downloadsOnButton' : 'setup.agent.downloadsLater')
            : why ? t('setup.agent.cantDownload', { reason: why })
                : t('setup.agent.notInstalledMore');

    return (
        <section className={`agent-choice${inSettings ? ' settings-card' : ''}`} aria-labelledby={titleId} aria-busy={saving || downloading || (!listing && !loadError)}>
            <h3 className={`agent-choice-title ${inSettings ? 'settings-label' : 'mc-job-title'}`} id={titleId}>
                {t('setup.agent.title')}
                {saving && <Loader2 size={13} className="spin" aria-hidden="true" />}
            </h3>
            {loadError && <p className="agent-choice-error" role="alert">{t('setup.agent.loadFailed', { error: loadError })}</p>}
            {listing && (
                <>
                    <RadioGroup className="setup-choices" aria-labelledby={titleId}>
                        {OPTIONS.map((o) => {
                            const p = program(o.id);
                            const unavailable = off(p);
                            const getIt = needsDownload(p);
                            const row = (
                                <button
                                    key={o.id}
                                    type="button"
                                    role="radio"
                                    aria-checked={selected === o.id}
                                    aria-disabled={unavailable || getIt || disabled || undefined}
                                    aria-describedby={unavailable || getIt ? offId : undefined}
                                    className={`setup-choice agent-choice-row${selected === o.id ? ' is-active' : ''}${unavailable ? ' is-disabled' : ''}${getIt ? ' is-pending' : ''}`}
                                    onClick={() => choose(o.id)}
                                >
                                    <span className="agent-choice-what">
                                        <bdi dir="ltr" className="agent-choice-name">{o.id}</bdi>
                                        <span className="agent-choice-label">{t(o.label)}</span>
                                        {p?.size_bytes != null && <bdi dir="ltr" className="agent-choice-size">{formatBytes(p.size_bytes)}</bdi>}
                                    </span>
                                    <span className="setup-choice-tag">{t(o.tag)}</span>
                                    {unavailable && <span className="setup-choice-tag agent-choice-off">{t('setup.agent.notInstalled')}</span>}
                                    {p && !installed(p) && !unavailable && !inSettings && (
                                        <span className="setup-choice-tag agent-choice-off is-quiet">{t('settings.model.notDownloaded')}</span>
                                    )}
                                </button>
                            );
                            // In view under the row: a greyed-out row that doesn't say why reads as broken.
                            if (unavailable) {
                                return (
                                    <div key={o.id} className="agent-choice-blocked">
                                        {row}
                                        <p id={offId} className="agent-choice-why" dir="auto">{cppNote}</p>
                                    </div>
                                );
                            }
                            // While its download runs, or after it failed, the card below has the bar or Retry instead.
                            if (!getIt || downloading || job?.state === 'failed') return row;
                            // Beside the radio, not inside it: a control inside a radio can't be reached.
                            return (
                                <div key={o.id} className="agent-choice-item">
                                    {row}
                                    <button
                                        type="button"
                                        className="btn-secondary agent-choice-download"
                                        onClick={() => void start()}
                                        disabled={saving || disabled}
                                        aria-label={t('setup.agent.downloadNamed', { name: o.id })}
                                    >
                                        <Download size={13} aria-hidden="true" /> {t('setup.agent.download')}
                                    </button>
                                </div>
                            );
                        })}
                    </RadioGroup>
                    {job && (job.state === 'downloading' || job.state === 'failed') && (
                        <AgentDownloadRow
                            job={job}
                            name="gaiacpp"
                            onRetry={() => void start()}
                            failedNote={t('setup.agent.stays', { name: listing.current })}
                            interrupted={interrupted}
                        />
                    )}
                    <button type="button" className="agent-choice-more" aria-expanded={open} aria-controls={moreId} onClick={() => setOpen((v) => !v)}>
                        <ChevronDown size={13} className={`agent-choice-chevron${open ? ' is-open' : ''}`} aria-hidden="true" /> {t('setup.agent.compare')}
                    </button>
                    {/* Kept mounted while hidden: the row waiting on Download is described by its note here. */}
                    <div id={moreId} className="agent-choice-detail" hidden={!open}>
                        {OPTIONS.map((o) => (
                            <p key={o.id}>
                                {t(o.more, { name: o.id })}
                                {o.id === 'gaiacpp' && cppNote && !off(cpp) && <> <span id={offId}>{cppNote}</span></>}
                            </p>
                        ))}
                        <p>{t(inSettings ? 'settings.agent.nextMessage' : 'setup.agent.hint')}</p>
                    </div>
                </>
            )}
            {saveError && <p className="agent-choice-error" role="alert">{t('setup.agent.saveFailed', { error: saveError })}</p>}
        </section>
    );
}
