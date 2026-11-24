// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { ChevronRight, Download, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { useT } from '../i18n/useT';
import { ref, type Param } from '../i18n/registry';
import { requestErrorParam } from '../utils/requestErrors';
import { log } from '../utils/logger';
import { useActiveAgentStore } from '../stores/activeAgentStore';
import { AgentDownloadRow, useAgentDownload } from './setup/AgentDownload';
import { WHY } from './setup/AgentChoice';
import type { AgentProblem, AgentProgram } from '../types';

const NAME = 'gaiacpp';

interface Props {
    problem: AgentProblem;
    /** Ask for the agent again, after a download or a switch. */
    onRetry: () => void;
    /** A message was sent while the agent can't start; it stayed in the box. */
    kept?: boolean;
}

/**
 * The chosen gaiacpp can't start: say why, and offer the two ways out. Nothing
 * changes until the user picks one.
 */
export function AgentRecovery({ problem, onRetry, kept = false }: Props) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [program, setProgram] = useState<AgentProgram | null>(null);
    const [checkError, setCheckError] = useState<Param | null>(null);
    const [switching, setSwitching] = useState(false);
    const [switchError, setSwitchError] = useState<Param | null>(null);
    const [retrying, setRetrying] = useState(false);
    const live = useRef(true);
    const retryRef = useRef(onRetry);
    retryRef.current = onRetry;

    const { job, start, follow, interrupted } = useAgentDownload(() => {
        if (!live.current) return;
        setRetrying(true);
        retryRef.current();
    });

    useEffect(() => {
        live.current = true;
        api.getAgentPrograms()
            .then((listing) => {
                if (!live.current) return;
                setProgram(listing.programs.find((p) => p.id === NAME) ?? null);
                follow(listing.download);
            })
            .catch((err: unknown) => {
                log.api.warn('Could not read whether gaiacpp can be downloaded', err);
                if (live.current) setCheckError(requestErrorParam(err));
            });
        return () => { live.current = false; };
    }, [follow]);

    // A fresh answer that still names a problem ends the wait, so the buttons work again.
    useEffect(() => setRetrying(false), [problem]);

    const switchToPython = async () => {
        setSwitching(true);
        setSwitchError(null);
        try {
            // Checked before saving: a Python agent that can't start must not replace the gaiacpp choice.
            const listing = await api.getAgentPrograms();
            if (!live.current) return;
            if (listing.programs.find((p) => p.id === 'gaiapy')?.installed !== true) {
                setSwitchError(ref('newChat.agent.pythonMissing', { name: NAME }));
                return;
            }
            await api.updateSettings({ flagship_agent: 'gaiapy' });
            useActiveAgentStore.getState().setCurrent('gaiapy');
            if (!live.current) return;
            setRetrying(true);
            onRetry();
        } catch (err) {
            if (live.current) setSwitchError(requestErrorParam(err));
        } finally {
            if (live.current) setSwitching(false);
        }
    };

    const downloading = job?.state === 'downloading';
    const busy = downloading || switching || retrying;
    const canDownload = program?.can_download === true;
    const reason: Param | null = !program || program.can_download
        ? null
        : program.unavailable_code ? ref(WHY[program.unavailable_code]) : program.unavailable_reason;

    return (
        <div className="new-chat-setup-failed agent-recovery" role="alert">
            <p className="new-chat-setup-failed-title">
                {t(problem.code === 'gaiacpp_invalid' ? 'newChat.agent.invalid' : 'newChat.agent.missing', { name: NAME })}
            </p>
            {kept && <p>{t('newChat.agent.kept')}</p>}
            {reason && <p className="new-chat-setup-failed-error">{t('newChat.agent.cantDownload', { name: NAME, reason })}</p>}
            {checkError && <p className="new-chat-setup-failed-error">{t('newChat.agent.checkFailed', { name: NAME, error: checkError })}</p>}
            {job && job.state !== 'idle' && (
                <AgentDownloadRow job={job} name={NAME} onRetry={() => void start()} interrupted={interrupted} />
            )}
            {retrying && !downloading && (
                <p className="agent-recovery-status" role="status">
                    <Loader2 size={13} className="spin" aria-hidden="true" /> {t('newChat.agent.starting')}
                </p>
            )}
            <div className="agent-recovery-actions">
                {canDownload && !job && (
                    <button type="button" className="btn-secondary" onClick={() => void start()} disabled={busy}>
                        <Download size={13} aria-hidden="true" /> {t('newChat.agent.downloadAgain', { name: NAME })}
                    </button>
                )}
                <button type="button" className="btn-secondary" onClick={() => void switchToPython()} disabled={busy}>
                    {t('newChat.agent.usePython')}
                </button>
            </div>
            {switchError && <p className="new-chat-setup-failed-error">{t('newChat.agent.switchFailed', { error: switchError })}</p>}
            <button type="button" className="new-chat-details" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                <ChevronRight size={12} className={open ? 'is-open' : ''} aria-hidden="true" /> {t('newChat.details')}
            </button>
            {open && <pre className="new-chat-details-text" dir="ltr">{problem.detail}</pre>}
        </div>
    );
}
