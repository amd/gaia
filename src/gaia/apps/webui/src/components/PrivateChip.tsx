// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Eye, EyeOff } from 'lucide-react';
import { Popover } from './Popover';
import { useT } from '../i18n/useT';
import { getMemorySettings } from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';

/**
 * New-chat memory choice, a small menu like the Ask chip: remember this chat, or
 * make it private so not even its first message is remembered. A menu, not a
 * toggle, so one stray click can't change it unseen.
 */
export function PrivateChip({ disabled }: { disabled?: boolean }) {
    const t = useT();
    const on = useChatStore((s) => s.draftPrivate);
    const setOn = useChatStore((s) => s.setDraftPrivate);
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const [memoryEnabled, setMemoryEnabled] = useState<boolean | null>(null);
    const [readFailed, setReadFailed] = useState(false);
    const [open, setOpen] = useState(false);
    const anchor = useRef<HTMLButtonElement>(null);

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

    const choose = (priv: boolean) => {
        setOn(priv);
        setOpen(false);
    };
    const options = [
        { priv: false, Icon: Eye, label: t('composer.remember'), note: t('composer.rememberNote') },
        { priv: true, Icon: EyeOff, label: t('composer.privateChat'), note: t('composer.privateChatNote') },
    ];
    return (
        <div className="composer-chip-wrap">
            <button
                ref={anchor}
                type="button"
                className={`composer-chip composer-chip-memory${on ? ' is-on' : ''}`}
                onClick={() => setOpen((v) => !v)}
                disabled={disabled}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-label={t('composer.memoryChipAria', {
                    state: on ? t('composer.private') : readFailed ? t('composer.memoryUnknown') : t('composer.memoryOn'),
                })}
                title={on
                    ? t('composer.privateChatNote')
                    : readFailed
                        ? t('composer.memoryUnknownTitle')
                        : t('composer.rememberNote')}
            >
                {on ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                <span className="composer-chip-label" dir="auto">{on ? t('composer.private') : readFailed ? t('composer.memoryUnknown') : t('composer.memoryOn')}</span>
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label={t('composer.memoryMenu')}>
                {options.map(({ priv, Icon, label, note }) => (
                    <button
                        key={String(priv)}
                        type="button"
                        className="popover-item"
                        onClick={() => choose(priv)}
                        aria-pressed={on === priv}
                    >
                        <span className="popover-item-check" aria-hidden="true">{on === priv ? <Check size={13} /> : <Icon size={13} />}</span>
                        <span className="popover-item-body">
                            <span className="popover-item-title">{label}</span>
                            <span className="popover-item-note">{note}</span>
                        </span>
                    </button>
                ))}
            </Popover>
        </div>
    );
}
