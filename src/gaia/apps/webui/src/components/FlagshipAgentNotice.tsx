// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { useT } from '../i18n/useT';
import { installFlagshipAgent } from '../services/flagshipInstall';

type InstallState =
    | { kind: 'idle' }
    | { kind: 'installing'; percent: number }
    | { kind: 'failed'; error: string };

/**
 * Shown when the backend has no GAIA agent (a fresh GAIA_HOME): every new chat
 * would be refused, so say why and offer the install instead.
 */
export function FlagshipAgentNotice({ onInstalled }: { onInstalled: () => void | Promise<void> }) {
    const t = useT();
    const [state, setState] = useState<InstallState>({ kind: 'idle' });
    const cancelled = useRef(false);
    useEffect(() => () => { cancelled.current = true; }, []);

    const install = useCallback(async () => {
        setState({ kind: 'installing', percent: 0 });
        try {
            await installFlagshipAgent({
                isCancelled: () => cancelled.current,
                onProgress: (s) => { if (!cancelled.current) setState({ kind: 'installing', percent: s.percent ?? 0 }); },
            });
            if (cancelled.current) return;
            await onInstalled();
            if (!cancelled.current) setState({ kind: 'idle' });
        } catch (err) {
            if (!cancelled.current) setState({ kind: 'failed', error: err instanceof Error ? err.message : String(err) });
        }
    }, [onInstalled]);

    return (
        <div className="app-notice is-warning" role="status">
            {state.kind === 'installing' ? (
                <span>{t('agentNotice.installing', { percent: Math.round(state.percent) })}</span>
            ) : state.kind === 'failed' ? (
                <span>{t('agentNotice.failed', { error: state.error })}</span>
            ) : (
                <span>{t('agentNotice.missing')}</span>
            )}
            {state.kind !== 'installing' && (
                <button type="button" className="link-btn" onClick={() => void install()}>
                    {state.kind === 'failed' ? t('agentNotice.retry') : t('agentNotice.install')}
                </button>
            )}
        </div>
    );
}
