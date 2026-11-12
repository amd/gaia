// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Loader2, Square, Volume2, X } from 'lucide-react';
import { useReadAloudStore } from '../stores/readAloudStore';
import { useT } from '../i18n/useT';

/** Reads one reply aloud, or stops it. */
export function ReadAloudButton({ messageId, content }: { messageId: string; content: string }) {
    const t = useT();
    const active = useReadAloudStore((s) => s.messageId === messageId);
    const status = useReadAloudStore((s) => (s.messageId === messageId ? s.status : null));
    const read = useReadAloudStore((s) => s.read);
    const stop = useReadAloudStore((s) => s.stop);

    const label = active ? t('readAloud.stop') : t('readAloud.read');
    return (
        <button
            type="button"
            className={`msg-action-btn msg-read-aloud${active ? ' is-active' : ''}`}
            onClick={() => (active ? stop() : void read(messageId, content))}
            title={label}
            aria-label={label}
            aria-pressed={active}
        >
            {status === 'loading' || status === 'downloading' ? <Loader2 size={12} className="spin" /> : active ? <Square size={12} /> : <Volume2 size={12} />}
        </button>
    );
}

/** While the first read downloads the voice model, says so under the reply. */
export function ReadAloudNote({ messageId }: { messageId: string }) {
    const t = useT();
    const downloading = useReadAloudStore((s) => s.messageId === messageId && s.status === 'downloading');
    const size = useReadAloudStore((s) => s.downloadGb);
    if (!downloading) return null;
    return (
        <p className="msg-read-aloud-note" role="status">
            {t('readAloud.downloading')}
            {size != null && <> <bdi dir="ltr">(~{size} GB)</bdi></>}
        </p>
    );
}

/** Why reading this reply aloud stopped, until dismissed. */
export function ReadAloudError({ messageId }: { messageId: string }) {
    const t = useT();
    const error = useReadAloudStore((s) => (s.error?.messageId === messageId ? s.error : null));
    const clear = useReadAloudStore((s) => s.clearError);
    if (!error) return null;
    return (
        <p className="msg-read-aloud-error" role="alert">
            <span>
                {t(`readAloud.error.${error.kind}`)}
                {error.detail && <span className="msg-read-aloud-detail"><bdi>{error.detail}</bdi></span>}
            </span>
            <button type="button" className="msg-action-btn" onClick={clear} aria-label={t('readAloud.dismiss')} title={t('readAloud.dismiss')}>
                <X size={12} />
            </button>
        </p>
    );
}
