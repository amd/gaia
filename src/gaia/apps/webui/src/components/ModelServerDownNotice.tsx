// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { useT } from '../i18n/useT';
import type { LemonadeTarget, LocalModelStatus } from '../types';

/** How often, and how long, a start is watched before it counts as failed. */
const POLL_MS = 2_000;
const START_LIMIT_MS = 90_000;

type Phase = 'idle' | 'starting' | 'switching' | 'back' | 'still_down';

/**
 * Under a turn that failed because the model server took no connection: the
 * fix GAIA can make from here, so the user is not left with text alone.
 * GAIA's own server can be started; a server the user runs can be swapped for
 * GAIA's own, unless LEMONADE_BASE_URL pins it.
 */
export function ModelServerDownNotice() {
    const t = useT();
    const [status, setStatus] = useState<LocalModelStatus | null>(null);
    const [target, setTarget] = useState<LemonadeTarget | null>(null);
    const [phase, setPhase] = useState<Phase>('idle');
    const [error, setError] = useState<string | null>(null);
    const alive = useRef(true);

    const check = useCallback(async (): Promise<LocalModelStatus | null> => {
        try {
            const next = await api.getLocalModelStatus();
            if (alive.current) setStatus(next);
            return next;
        } catch (err) {
            if (alive.current) setError(err instanceof Error ? err.message : String(err));
            return null;
        }
    }, []);

    useEffect(() => {
        alive.current = true;
        void check();
        api.getLemonadeTarget()
            .then((next) => { if (alive.current) setTarget(next); })
            .catch((err) => { if (alive.current) setError(err instanceof Error ? err.message : String(err)); });
        return () => { alive.current = false; };
    }, [check]);

    const checkAgain = useCallback(async () => {
        setError(null);
        const next = await check();
        if (next) setPhase(next.state === 'stopped' ? 'still_down' : 'back');
    }, [check]);

    const start = useCallback(async () => {
        setPhase('starting');
        setError(null);
        try {
            await api.restartLocalModel(false);
        } catch (err) {
            if (!alive.current) return;
            setError(err instanceof Error ? err.message : String(err));
            setPhase('idle');
            return;
        }
        const until = Date.now() + START_LIMIT_MS;
        while (alive.current && Date.now() < until) {
            await new Promise((resolve) => setTimeout(resolve, POLL_MS));
            const next = await check();
            if (!alive.current) return;
            if (next?.restart_error) {
                setError(next.restart_error);
                setPhase('idle');
                return;
            }
            if (next && next.state !== 'stopped' && !next.restarting) {
                setPhase('back');
                return;
            }
        }
        if (alive.current) setPhase('still_down');
    }, [check]);

    const switchToOwn = useCallback(async () => {
        setPhase('switching');
        setError(null);
        try {
            const next = await api.setLemonadeTarget({ mode: 'gaia' });
            if (!alive.current) return;
            setTarget(next);
            setPhase('back');
        } catch (err) {
            if (!alive.current) return;
            setError(err instanceof Error ? err.message : String(err));
            setPhase('idle');
        }
    }, []);

    const busy = phase === 'starting' || phase === 'switching';
    const ours = status?.owner === 'gaia';
    // LEMONADE_BASE_URL outranks a saved choice, so only unsetting it switches.
    const canSwitch = !ours && target?.mode === 'external' && target.source !== 'env';

    if (phase === 'back') {
        return (
            <div className="server-down-notice server-down-notice--ok" role="status">
                <p>{t('serverDown.back')}</p>
            </div>
        );
    }
    return (
        <div className="server-down-notice" role="alert">
            <strong className="server-down-notice__title">
                <AlertTriangle size={14} aria-hidden="true" /> {t('serverDown.title')}
            </strong>
            <p>{t('serverDown.body')}</p>
            {status && !ours && !canSwitch && <p>{t('serverDown.notOurs')}</p>}
            {phase === 'still_down' && <p>{t('serverDown.stillDown')}</p>}
            {error && <p><bdi dir="ltr" className="server-down-notice__raw">{error}</bdi></p>}
            <div className="server-down-notice__actions">
                {ours && (
                    <button type="button" className="btn-primary" disabled={busy} onClick={() => void start()}>
                        {phase === 'starting'
                            ? <><Loader2 size={13} className="btn-spinner" aria-hidden="true" /> {t('serverDown.starting')}</>
                            : t('serverDown.start')}
                    </button>
                )}
                {canSwitch && (
                    <button type="button" className="btn-primary" disabled={busy} onClick={() => void switchToOwn()}>
                        {phase === 'switching'
                            ? <><Loader2 size={13} className="btn-spinner" aria-hidden="true" /> {t('serverDown.switching')}</>
                            : t('serverDown.useOwn')}
                    </button>
                )}
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void checkAgain()}>
                    {t('settings.key.checkAgain')}
                </button>
            </div>
        </div>
    );
}
