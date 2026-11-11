// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { useT } from '../i18n/useT';
import { getMemorySettings } from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';

/** New-chat toggle: the chat is created private, so not even its first message is remembered. */
export function PrivateChip({ disabled }: { disabled?: boolean }) {
    const t = useT();
    const on = useChatStore((s) => s.draftPrivate);
    const setOn = useChatStore((s) => s.setDraftPrivate);
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const [memoryEnabled, setMemoryEnabled] = useState<boolean | null>(null);
    const [readFailed, setReadFailed] = useState(false);

    // Re-read on close: Settings can switch memory off while this screen stays mounted.
    useEffect(() => {
        if (settingsOpen) return;
        let cancelled = false;
        getMemorySettings()
            .then((s) => {
                if (cancelled) return;
                setMemoryEnabled(s.memory_enabled);
                setReadFailed(false);
            })
            .catch((err) => {
                if (cancelled) return;
                log.ui.error('Could not read the memory setting for the Private chip', err);
                setMemoryEnabled(null);
                setReadFailed(true);
            });
        return () => { cancelled = true; };
    }, [settingsOpen]);

    // A hidden Private choice would make the next chat private for good, unseen.
    useEffect(() => {
        if (memoryEnabled === false && on) setOn(false);
    }, [memoryEnabled, on, setOn]);

    if (memoryEnabled === false) {
        return (
            <button
                type="button"
                className="composer-chip composer-chip-memory is-unavailable"
                disabled
                aria-label={t('composer.memoryOff')}
                title={t('composer.memoryOffTitle')}
            >
                <EyeOff size={13} aria-hidden="true" />
                <span className="composer-chip-label" dir="auto">{t('composer.memoryOff')}</span>
            </button>
        );
    }

    return (
        <button
            type="button"
            className={`composer-chip composer-chip-memory${on ? ' is-on' : ''}`}
            onClick={() => setOn(!on)}
            disabled={disabled}
            aria-label={on ? t('composer.privateOff') : t('composer.privateOn')}
            title={on
                ? t('composer.privateOffTitle')
                : readFailed
                    ? t('composer.memoryUnknownTitle')
                    : t('composer.privateOnTitle')}
        >
            {on ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
            <span className="composer-chip-label" dir="auto">{on ? t('composer.private') : readFailed ? t('composer.memoryUnknown') : t('composer.memoryOn')}</span>
        </button>
    );
}
