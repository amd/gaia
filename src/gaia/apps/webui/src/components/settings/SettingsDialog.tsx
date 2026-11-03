// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useEffect, useRef } from 'react';
import { Loader2, X } from 'lucide-react';
import { useChatStore, type SettingsSection } from '../../stores/chatStore';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { GeneralSettings } from './GeneralSettings';
import { ModelSettings } from './ModelSettings';
import { PermissionsSettings } from './PermissionsSettings';
import { SkillsSettings } from './SkillsSettings';
import { MemorySettings } from './MemorySettings';
import { PrivacySettings } from './PrivacySettings';
import { NotificationSettings } from './NotificationSettings';
import '../SettingsModal.css';
import './SettingsDialog.css';
import { useT } from '../../i18n/useT';

// The two heaviest panes load on first open.
const ConnectorsPane = lazy(() => import('./ConnectorsPane').then((m) => ({ default: m.ConnectorsPane })));
const AdvancedPane = lazy(() => import('./AdvancedSettings').then((m) => ({ default: m.AdvancedSettings })));

const SECTIONS: SettingsSection[] = [
    'general', 'model', 'permissions', 'skills', 'connectors', 'memory', 'notifications', 'privacy', 'advanced',
];

/** One settings window with a section list on the left. */
export function SettingsDialog({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const t = useT();
    const section = useChatStore((s) => s.settingsSection);
    const open = useChatStore((s) => s.openSettings);
    const close = useChatStore((s) => s.closeSettings);
    const panel = useRef<HTMLDivElement>(null);
    const returnFocus = useRef<Element | null>(null);

    const isOpen = section !== null;
    useEscapeToClose(isOpen, close);
    useFocusTrap(panel, isOpen);
    useEffect(() => {
        if (!isOpen) return;
        returnFocus.current = document.activeElement;
        return () => {
            (returnFocus.current as HTMLElement | null)?.focus?.();
        };
    }, [isOpen, close]);

    // Focus moves into the dialog once, when it opens.
    useEffect(() => {
        if (isOpen) panel.current?.querySelector<HTMLElement>('.settings-nav-item.is-active')?.focus();
    }, [isOpen]);

    if (!section) return null;

    const pane = (() => {
        switch (section) {
            case 'general': return <GeneralSettings />;
            case 'model': return <ModelSettings />;
            case 'permissions': return <PermissionsSettings />;
            case 'skills': return <SkillsSettings onUseSkill={(name) => { close(); onUseSkill(name); }} />;
            case 'connectors': return <ConnectorsPane />;
            case 'memory': return <MemorySettings />;
            case 'notifications': return <NotificationSettings />;
            case 'privacy': return <PrivacySettings />;
            case 'advanced': return <AdvancedPane />;
        }
    })();

    return (
        <div className="settings-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
            <div ref={panel} className="settings-dialog" role="dialog" aria-modal="true" aria-label={t('settings.title')}>
                <nav className="settings-nav" aria-label={t('settings.sections')}>
                    <h1 className="settings-nav-title">{t('settings.title')}</h1>
                    {SECTIONS.map((id) => (
                        <button
                            key={id}
                            type="button"
                            data-section={id}
                            className={`settings-nav-item${section === id ? ' is-active' : ''}`}
                            aria-current={section === id ? 'page' : undefined}
                            // The focus ring follows the selection instead of staying on the tab focused at open.
                            onClick={(e) => { e.currentTarget.focus(); open(id); }}
                        >
                            {t(`settings.section.${id}`)}
                        </button>
                    ))}
                </nav>
                <div className="settings-body">
                    <button type="button" className="settings-close" onClick={close} aria-label={t('settings.close')}>
                        <X size={18} />
                    </button>
                    <div className="settings-scroll">
                        <Suspense fallback={<p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}>
                            {pane}
                        </Suspense>
                    </div>
                </div>
            </div>
        </div>
    );
}
