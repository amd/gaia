// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useEffect, useRef } from 'react';
import { Loader2, X } from 'lucide-react';
import { useChatStore, type SettingsSection } from '../../stores/chatStore';
import { GeneralSettings } from './GeneralSettings';
import { ModelSettings } from './ModelSettings';
import { PermissionsSettings } from './PermissionsSettings';
import { SkillsSettings } from './SkillsSettings';
import { MemorySettings } from './MemorySettings';
import { PrivacySettings } from './PrivacySettings';
import '../SettingsModal.css';
import './SettingsDialog.css';

// The two heaviest panes load on first open.
const ConnectorsPane = lazy(() => import('./ConnectorsPane').then((m) => ({ default: m.ConnectorsPane })));
const AdvancedPane = lazy(() => import('./AdvancedSettings').then((m) => ({ default: m.AdvancedSettings })));

const SECTIONS: Array<{ id: SettingsSection; label: string }> = [
    { id: 'general', label: 'General' },
    { id: 'model', label: 'Model' },
    { id: 'permissions', label: 'Permissions' },
    { id: 'skills', label: 'Skills' },
    { id: 'connectors', label: 'Connectors' },
    { id: 'memory', label: 'Memory' },
    { id: 'privacy', label: 'Privacy' },
    { id: 'advanced', label: 'Advanced' },
];

/** One settings window with a section list on the left. */
export function SettingsDialog({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const section = useChatStore((s) => s.settingsSection);
    const open = useChatStore((s) => s.openSettings);
    const close = useChatStore((s) => s.closeSettings);
    const panel = useRef<HTMLDivElement>(null);
    const returnFocus = useRef<Element | null>(null);

    const isOpen = section !== null;
    useEffect(() => {
        if (!isOpen) return;
        returnFocus.current = document.activeElement;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('keydown', onKey);
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
            case 'privacy': return <PrivacySettings />;
            case 'advanced': return <AdvancedPane />;
        }
    })();

    return (
        <div className="settings-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
            <div ref={panel} className="settings-dialog" role="dialog" aria-modal="true" aria-label="Settings">
                <nav className="settings-nav" aria-label="Settings sections">
                    <h1 className="settings-nav-title">Settings</h1>
                    {SECTIONS.map((s) => (
                        <button
                            key={s.id}
                            type="button"
                            data-section={s.id}
                            className={`settings-nav-item${section === s.id ? ' is-active' : ''}`}
                            aria-current={section === s.id ? 'page' : undefined}
                            onClick={() => open(s.id)}
                        >
                            {s.label}
                        </button>
                    ))}
                </nav>
                <div className="settings-body">
                    <button type="button" className="settings-close" onClick={close} aria-label="Close settings">
                        <X size={18} />
                    </button>
                    <Suspense fallback={<p className="settings-hint"><Loader2 size={13} className="spin" /> Loading…</p>}>
                        {pane}
                    </Suspense>
                </div>
            </div>
        </div>
    );
}
