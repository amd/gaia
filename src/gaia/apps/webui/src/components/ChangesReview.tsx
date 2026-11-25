// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, X } from 'lucide-react';
import * as api from '../services/api';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useT, type Translate } from '../i18n/useT';
import type { MessageKey } from '../i18n/types';
import type { FileDiff, TurnChanges } from '../types/changes';
import { DiffCard } from './render/DiffCard';

const CHANGE_LABEL: Record<FileDiff['change'], MessageKey> = {
    edited: 'changes.edited',
    created: 'changes.created',
    deleted: 'changes.deleted',
};

function note(file: FileDiff, t: Translate): string | undefined {
    const sizes = { before: file.before_size, after: file.after_size };
    if (file.too_large) return t('changes.tooLarge', sizes);
    if (file.binary) return t('changes.binary', sizes);
    if (file.truncated) return t('changes.truncated');
    return undefined;
}

/** What a turn did to each file, from the copies GAIA saved before and after it. */
export function ChangesReview({ sessionId, turn, onClose }: {
    sessionId: string;
    turn: TurnChanges;
    onClose: () => void;
}) {
    const t = useT();
    const [files, setFiles] = useState<FileDiff[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEscapeToClose(true, onClose);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, true);

    useEffect(() => {
        let live = true;
        api.getTurnDiff(sessionId, turn.turn_id).then(
            (res) => { if (live) setFiles(res.files); },
            (err: unknown) => { if (live) setError(err instanceof Error ? err.message : String(err)); },
        );
        return () => { live = false; };
    }, [sessionId, turn.turn_id]);

    // Portaled: opened from a message, a transformed ancestor would otherwise
    // clip the fixed backdrop to the chat column.
    return createPortal(
        <div ref={dialogRef} className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={t('changes.label')}>
            <div className="modal-panel changes-review" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>{t('changes.title')}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label={t('changes.close')}>
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body changes-review-body">
                    {error && <p className="settings-error" role="alert">{t('changes.loadFailed', { error })}</p>}
                    {!files && !error && (
                        <p className="changes-review-loading"><Loader2 size={13} className="spin" /> {t('changes.loading')}</p>
                    )}
                    {files?.map((file) => (
                        <section key={file.path} className="changes-review-file">
                            <DiffCard
                                data={{
                                    path: file.path,
                                    badge: t(CHANGE_LABEL[file.change]),
                                    note: note(file, t),
                                    unified: file.binary || file.too_large ? '' : file.unified,
                                }}
                            />
                        </section>
                    ))}
                    {turn.restored_at && (
                        <p className="settings-hint">{t('changes.undone')}</p>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
