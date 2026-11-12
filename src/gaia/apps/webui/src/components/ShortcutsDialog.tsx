// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import type { MessageKey } from '../i18n/registry';
import { useT } from '../i18n/useT';
import { formatShortcut } from '../utils/platform';
import './ShortcutsDialog.css';

/** Every shortcut the app answers, in the words and keys this platform uses. */
export const SHORTCUTS: ReadonlyArray<{ label: MessageKey; keys: string }> = [
    { label: 'shortcuts.newChat', keys: formatShortcut('Mod+Shift+O') },
    { label: 'shortcuts.search', keys: formatShortcut('Mod+K') },
    { label: 'shortcuts.sidebar', keys: formatShortcut('Mod+B') },
    { label: 'shortcuts.settings', keys: formatShortcut('Mod+,') },
    { label: 'shortcuts.sheet', keys: formatShortcut('Mod+/') },
    { label: 'shortcuts.send', keys: 'Enter' },
    { label: 'shortcuts.newLine', keys: 'Shift+Enter' },
    { label: 'shortcuts.stop', keys: 'Esc' },
];

/** The keyboard shortcuts sheet (Ctrl+/ or ⌘/). */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
    const t = useT();
    const dialogRef = useRef<HTMLDivElement>(null);
    useEscapeToClose(true, onClose);
    useFocusTrap(dialogRef, true);
    useDialogFocus(dialogRef, true);

    // Rendered at the app's top level, inside #root, so the Arabic letter-spacing reset applies.
    return (
        <div ref={dialogRef} className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={t('shortcuts.title')}>
            <div className="modal-panel shortcuts-dialog" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>{t('shortcuts.title')}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label={t('shortcuts.close')}>
                        <X size={18} aria-hidden="true" />
                    </button>
                </div>
                <dl className="modal-body shortcuts-list">
                    {SHORTCUTS.map(({ label, keys }) => (
                        <div key={label} className="shortcuts-row">
                            <dt>{t(label)}</dt>
                            <dd><kbd dir="ltr">{keys}</kbd></dd>
                        </div>
                    ))}
                </dl>
            </div>
        </div>
    );
}
