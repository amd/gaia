// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Children, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useT } from '../i18n/useT';
import { UI_HEADER } from '../services/api';
import { markStrayDirection } from './CalendarApproval';
import './AttachmentLink.css';

const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;
const PREVIEW_BYTES = 200_000;

type Shown =
    | { kind: 'loading' }
    | { kind: 'text'; text: string; cut: boolean }
    | { kind: 'image'; src: string }
    | { kind: 'none' }
    | { kind: 'failed' };

function fileName(href: string): string {
    const last = href.split('/').pop() ?? href;
    try { return decodeURIComponent(last); } catch { return last; }
}

function AttachmentPreview({ href, name, onClose }: { href: string; name: string; onClose: () => void }) {
    const t = useT();
    const panel = useRef<HTMLDivElement>(null);
    const closeButton = useRef<HTMLButtonElement>(null);
    const [shown, setShown] = useState<Shown>({ kind: 'loading' });

    useEscapeToClose(true, onClose);
    useFocusTrap(panel, true);
    useEffect(() => { closeButton.current?.focus(); }, []);

    // Read through the app: the backend refuses a plain browser window, which has no app token.
    useEffect(() => {
        const abort = new AbortController();
        let objectUrl: string | null = null;
        (async () => {
            try {
                const res = await fetch(href, { headers: UI_HEADER, signal: abort.signal });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const bytes = new Uint8Array(await res.arrayBuffer());
                if (IMAGE_NAME.test(href)) {
                    objectUrl = URL.createObjectURL(new Blob([bytes], { type: res.headers.get('content-type') ?? undefined }));
                    setShown({ kind: 'image', src: objectUrl });
                    return;
                }
                const cut = bytes.length > PREVIEW_BYTES;
                const text = new TextDecoder('utf-8').decode(bytes.subarray(0, PREVIEW_BYTES), { stream: true });
                // PDFs, Office files and the like are not text; a replacement or NUL character gives them away.
                setShown(/[\u0000�]/.test(text) ? { kind: 'none' } : { kind: 'text', text, cut });
            } catch (err) {
                if (!abort.signal.aborted) setShown({ kind: 'failed' });
            }
        })();
        return () => {
            abort.abort();
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [href]);

    return createPortal(
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={t('attachment.dialog')} onClick={onClose}>
            <div ref={panel} className="modal-panel attachment-preview" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3 dir="auto"><bdi>{name}</bdi></h3>
                    <button ref={closeButton} type="button" className="btn-icon" onClick={onClose} aria-label={t('imageViewer.close')}>
                        <X size={18} aria-hidden="true" />
                    </button>
                </div>
                <div className="modal-body attachment-preview-body">
                    {shown.kind === 'loading' && <p role="status">{t('attachment.loading', { name })}</p>}
                    {shown.kind === 'failed' && <p role="alert">{t('attachment.failed', { name })}</p>}
                    {shown.kind === 'none' && <p>{t('attachment.noPreview', { name })}</p>}
                    {shown.kind === 'image' && <img className="attachment-preview-image" src={shown.src} alt={name} />}
                    {shown.kind === 'text' && (
                        <>
                            <pre className="attachment-preview-text" dir="auto">{markStrayDirection(shown.text)}</pre>
                            {shown.cut && <p className="attachment-preview-note">{t('attachment.cut')}</p>}
                        </>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}

/** A link to a file the user attached. It opens inside the app, never in the system browser. */
export function AttachmentLink({ href, children }: { href: string; children: ReactNode }) {
    const [open, setOpen] = useState(false);
    // The link text is the name the user gave it; the URL holds a stored name.
    const label = Children.toArray(children).filter((c) => typeof c === 'string').join('').trim();
    const name = label || fileName(href);
    return (
        <>
            <button type="button" className="md-link attachment-link" onClick={() => setOpen(true)}>{children}</button>
            {open && <AttachmentPreview href={href} name={name} onClose={() => setOpen(false)} />}
        </>
    );
}
