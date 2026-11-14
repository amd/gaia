// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';

const LINKS: Array<{ label: MessageKey; href: string }> = [
    { label: 'settings.about.whatsNew', href: 'https://github.com/amd/gaia/releases' },
    { label: 'settings.about.docs', href: 'https://amd-gaia.ai/docs' },
    { label: 'settings.about.report', href: 'https://github.com/amd/gaia/issues/new/choose' },
    { label: 'settings.about.license', href: 'https://github.com/amd/gaia/blob/main/LICENSE.md' },
];

/** The app's version, where to read more, and how to report a problem. */
export function AboutSettings() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    // A name and a number, the same in every language.
    const versionLabel = `GAIA v${__APP_VERSION__}`;
    const [copied, setCopied] = useState(false);
    const [copyError, setCopyError] = useState<string | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

    const copy = useCallback(async () => {
        setCopyError(null);
        try {
            await navigator.clipboard.writeText(versionLabel);
            setCopied(true);
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            setCopyError(t('settings.about.copyFailed', { reason: err instanceof Error ? err.message : String(err) }));
        }
    }, [t, versionLabel]);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.about')}</h2>
            <section className="settings-card" aria-labelledby="about-version">
                <div className="settings-row settings-row-between">
                    <span>
                        <strong id="about-version"><bdi dir="ltr">{versionLabel}</bdi></strong>{' '}
                        <span className="beta-badge">BETA</span>
                    </span>
                    <button type="button" className="btn-secondary" onClick={() => void copy()}>
                        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                        {copied ? t('settings.about.copied') : t('settings.about.copy')}
                    </button>
                </div>
                <p className="settings-hint">{t('settings.advanced.about')}</p>
                {copyError && <p className="settings-error" role="alert">{copyError}</p>}
            </section>
            <section className="settings-card" aria-label={t('settings.about.links')}>
                <ul className="settings-about-links">
                    {LINKS.map((link) => (
                        <li key={link.href}>
                            <a href={link.href} target="_blank" rel="noopener noreferrer">
                                {t(link.label)} <ExternalLink size={12} aria-hidden="true" />
                            </a>
                        </li>
                    ))}
                </ul>
                <p className="settings-hint">
                    {t('settings.about.reportHint')}{' '}
                    <button type="button" className="link-btn" onClick={() => openSettings('privacy')}>
                        {t('settings.about.reportWhere')}
                    </button>
                </p>
            </section>
        </div>
    );
}
