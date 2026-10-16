// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import * as api from '../services/api';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import type { FileDiff, TurnChanges } from '../types/changes';
import { DiffCard } from './render/DiffCard';

const CHANGE_LABEL: Record<FileDiff['change'], string> = {
    edited: 'Edited',
    created: 'Created',
    deleted: 'Deleted',
};

function note(file: FileDiff): string | undefined {
    if (file.too_large) return `Too large to show: ${file.before_size} → ${file.after_size} bytes.`;
    if (file.binary) return `Binary file: ${file.before_size} → ${file.after_size} bytes. No text diff.`;
    if (file.truncated) return 'Diff shortened: the change is too long to show in full.';
    return undefined;
}

/** What a turn did to each file, from the copies GAIA saved before and after it. */
export function ChangesReview({ sessionId, turn, onClose }: {
    sessionId: string;
    turn: TurnChanges;
    onClose: () => void;
}) {
    const [files, setFiles] = useState<FileDiff[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEscapeToClose(true, onClose);

    useEffect(() => {
        let live = true;
        api.getTurnDiff(sessionId, turn.turn_id).then(
            (res) => { if (live) setFiles(res.files); },
            (err: unknown) => { if (live) setError(err instanceof Error ? err.message : String(err)); },
        );
        return () => { live = false; };
    }, [sessionId, turn.turn_id]);

    return (
        <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Review changes">
            <div className="modal-panel changes-review" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Changes in this turn</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body changes-review-body">
                    {error && <p className="settings-error" role="alert">Could not load the changes: {error}</p>}
                    {!files && !error && (
                        <p className="changes-review-loading"><Loader2 size={13} className="spin" /> Loading changes…</p>
                    )}
                    {files?.map((file) => (
                        <section key={file.path} className="changes-review-file">
                            <DiffCard
                                data={{
                                    title: file.path,
                                    badge: CHANGE_LABEL[file.change],
                                    note: note(file),
                                    unified: file.binary || file.too_large ? '' : file.unified,
                                }}
                            />
                        </section>
                    ))}
                    {turn.restored_at && (
                        <p className="settings-hint">This turn was undone; these are the changes it had made.</p>
                    )}
                </div>
            </div>
        </div>
    );
}
