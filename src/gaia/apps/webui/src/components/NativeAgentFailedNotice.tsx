// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { Loader2, Settings } from 'lucide-react';
import * as api from '../services/api';
import { useT } from '../i18n/useT';
import { ref, type Param } from '../i18n/registry';
import { useChatStore } from '../stores/chatStore';
import { useActiveAgentStore } from '../stores/activeAgentStore';
import { requestErrorParam } from '../utils/requestErrors';

const NAME = 'gaiacpp';

/**
 * Under a turn that failed on gaiacpp: say which agent this chat runs on and
 * offer the way back to the Python one. Nothing switches until the user asks.
 */
export function NativeAgentFailedNotice() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const [switching, setSwitching] = useState(false);
    const [switched, setSwitched] = useState(false);
    const [error, setError] = useState<Param | null>(null);
    const live = useRef(true);
    useEffect(() => {
        live.current = true;
        return () => { live.current = false; };
    }, []);

    const usePython = async () => {
        setSwitching(true);
        setError(null);
        try {
            // Checked before saving: a Python agent that can't start must not replace the gaiacpp choice.
            const listing = await api.getAgentPrograms();
            if (!live.current) return;
            if (listing.programs.find((p) => p.id === 'gaiapy')?.installed !== true) {
                setError(ref('newChat.agent.pythonMissing', { name: NAME }));
                return;
            }
            await api.updateSettings({ flagship_agent: 'gaiapy' });
            useActiveAgentStore.getState().setCurrent('gaiapy');
            if (live.current) setSwitched(true);
        } catch (err) {
            if (live.current) setError(requestErrorParam(err));
        } finally {
            if (live.current) setSwitching(false);
        }
    };

    if (switched) {
        return (
            <div className="server-down-notice server-down-notice--ok native-agent-notice" role="status">
                <p>{t('nativeFailed.switched')}</p>
            </div>
        );
    }
    return (
        <div className="server-down-notice native-agent-notice" role="note">
            <p>{t('nativeFailed.body', { name: NAME })}</p>
            {error && <p role="alert">{t('newChat.agent.switchFailed', { error })}</p>}
            <div className="server-down-notice__actions">
                <button type="button" className="btn-secondary" disabled={switching} onClick={() => void usePython()}>
                    {switching && <Loader2 size={13} className="btn-spinner" aria-hidden="true" />} {t('newChat.agent.usePython')}
                </button>
                <button type="button" className="link-btn native-agent-notice__settings" disabled={switching} onClick={() => openSettings('model')}>
                    <Settings size={12} aria-hidden="true" /> {t.compose((say) => say('failedTurn.openSettings', { tab: say('settings.section.model') }))}
                </button>
            </div>
        </div>
    );
}
