// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useEffect, useRef } from 'react';
import { ChevronLeft, Loader2, X } from 'lucide-react';
import { useChatStore, type SettingsSection } from '../../stores/chatStore';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { useDialogFocus } from '../../hooks/useDialogFocus';
import { GeneralSettings } from './GeneralSettings';
import { ModelSettings } from './ModelSettings';
import { PermissionsSettings } from './PermissionsSettings';
import { SkillsSettings } from './SkillsSettings';
import { MemorySettings } from './MemorySettings';
import { PrivacySettings } from './PrivacySettings';
import { NotificationSettings } from './NotificationSettings';
import { ReadAloudSettings } from './ReadAloudSettings';
import '../SettingsModal.css';
import './SettingsDialog.css';
import { useT } from '../../i18n/useT';
import { SetupSettings } from './SetupSettings';
import { ErrorBoundary } from '../ErrorBoundary';

// The heaviest panes, and ones most people never open, load on first open.
const ConnectorsPane = lazy(() => import('./ConnectorsPane').then((m) => ({ default: m.ConnectorsPane })));
const MessagingPane = lazy(() => import('./MessagingSettings').then((m) => ({ default: m.MessagingSettings })));
const AutomationsPane = lazy(() => import('./AutomationsSettings').then((m) => ({ default: m.AutomationsSettings })));
const AdvancedPane = lazy(() => import('./AdvancedSettings').then((m) => ({ default: m.AdvancedSettings })));

const SECTIONS: SettingsSection[] = [
    'general', 'setup', 'model', 'permissions', 'skills', 'connectors', 'messaging', 'automations', 'memory', 'notifications', 'voice', 'privacy', 'advanced',
];

/** One settings window with a section list on the left. */
export function SettingsDialog({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const t = useT();
    const section = useChatStore((s) => s.settingsSection);
    const open = useChatStore((s) => s.openSettings);
    const close = useChatStore((s) => s.closeSettings);
    const jump = useChatStore((s) => s.settingsJump);
    const panel = useRef<HTMLDivElement>(null);
    const scroller = useRef<HTMLDivElement>(null);

    const isOpen = section !== null;
    useEscapeToClose(isOpen, close);
    useFocusTrap(panel, isOpen);
    // Focus starts on the active section's nav item and returns on close.
    useDialogFocus(panel, isOpen, () =>
        panel.current?.querySelector<HTMLElement>('.settings-nav-item.is-active'),
    );

    // Panes load their data first, so wait for the setting to appear before landing on it.
    useEffect(() => {
        const root = scroller.current;
        if (!jump || jump.section !== section || !root) return;
        let unflash: ReturnType<typeof setTimeout> | undefined;
        const land = (): boolean => {
            const hit = root.querySelector<HTMLElement>(jump.target);
            if (!hit) return false;
            for (let d = hit.closest('details'); d; d = d.parentElement?.closest('details') ?? null) d.open = true;
            const box = hit.closest<HTMLElement>('.model-in-use-field, .settings-field, details, .settings-card') ?? hit;
            box.classList.add('settings-target-hit');
            box.scrollIntoView({ block: 'start' });
            const focusTo = hit instanceof HTMLDetailsElement ? hit.querySelector('summary') : hit;
            if (focusTo) {
                if (!focusTo.matches('summary, button, input, select, textarea, a[href], [tabindex]')) focusTo.tabIndex = -1;
                focusTo.focus({ preventScroll: true });
            }
            unflash = setTimeout(() => box.classList.remove('settings-target-hit'), 1600);
            return true;
        };
        if (land()) return () => clearTimeout(unflash);
        const watch = new MutationObserver(() => { if (land()) watch.disconnect(); });
        watch.observe(root, { childList: true, subtree: true });
        const giveUp = setTimeout(() => watch.disconnect(), 5000);
        return () => { watch.disconnect(); clearTimeout(giveUp); clearTimeout(unflash); };
    }, [jump, section]);

    if (!section) return null;

    const pane = (() => {
        switch (section) {
            case 'general': return <GeneralSettings />;
            case 'setup': return <SetupSettings />;
            case 'model': return <ModelSettings />;
            case 'permissions': return <PermissionsSettings />;
            case 'skills': return <SkillsSettings onUseSkill={(name) => { close(); onUseSkill(name); }} />;
            case 'connectors': return <ConnectorsPane />;
            case 'messaging': return <MessagingPane />;
            case 'automations': return <AutomationsPane />;
            case 'memory': return <MemorySettings />;
            case 'notifications': return <NotificationSettings />;
            case 'voice': return <ReadAloudSettings />;
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
                    <div ref={scroller} className="settings-scroll">
                        {jump && jump.section === section && (
                            <button
                                type="button"
                                className="settings-back"
                                onClick={() => {
                                    open(jump.from);
                                    panel.current?.querySelector<HTMLElement>(`[data-section="${jump.from}"]`)?.focus();
                                }}
                            >
                                <ChevronLeft size={14} aria-hidden="true" />
                                {t.compose((say) => say('settings.backTo', { section: say(`settings.section.${jump.from}`) }))}
                            </button>
                        )}
                        {/* Keyed so a crashed section clears when another is picked. */}
                        <ErrorBoundary key={section} area={`settings.${section}`}>
                            <Suspense fallback={<p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}>
                                {pane}
                            </Suspense>
                        </ErrorBoundary>
                    </div>
                </div>
            </div>
        </div>
    );
}
