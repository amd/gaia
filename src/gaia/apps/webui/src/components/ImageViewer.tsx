// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useT } from '../i18n/useT';
import './ImageViewer.css';

export interface ViewerImage {
    src: string;
    name: string;
}

interface ImageViewerProps {
    images: readonly ViewerImage[];
    index: number;
    onIndex: (index: number) => void;
    onClose: () => void;
}

/** One image fitted to the window, over the page; steps through the other images of its message. */
export function ImageViewer({ images, index, onIndex, onClose }: ImageViewerProps) {
    const t = useT();
    const panel = useRef<HTMLDivElement>(null);
    const closeButton = useRef<HTMLButtonElement>(null);
    const many = images.length > 1;
    const image = images[index];

    useEscapeToClose(true, onClose);
    useFocusTrap(panel, true);
    useEffect(() => { closeButton.current?.focus(); }, []);

    // Arrows point the way the layout runs: in a right-to-left page, left is "next".
    const step = useCallback((by: number) => {
        onIndex((index + by + images.length) % images.length);
    }, [index, images.length, onIndex]);

    useEffect(() => {
        if (!many) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            const rtl = panel.current ? getComputedStyle(panel.current).direction === 'rtl' : false;
            e.preventDefault();
            step((e.key === 'ArrowRight') !== rtl ? 1 : -1);
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [many, step]);

    if (!image) return null;
    return createPortal(
        <div
            ref={panel}
            className="image-viewer"
            role="dialog"
            aria-modal="true"
            aria-label={t('imageViewer.dialog')}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="image-viewer-stage" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
                <button ref={closeButton} type="button" className="image-viewer-btn image-viewer-close" onClick={onClose} aria-label={t('imageViewer.close')}>
                    <X size={18} aria-hidden="true" />
                </button>
                {many && (
                    <button type="button" className="image-viewer-btn image-viewer-prev" onClick={() => step(-1)} aria-label={t('imageViewer.prev')}>
                        <ChevronLeft size={20} aria-hidden="true" />
                    </button>
                )}
                <img className="image-viewer-img" src={image.src} alt={image.name} />
                {many && (
                    <button type="button" className="image-viewer-btn image-viewer-next" onClick={() => step(1)} aria-label={t('imageViewer.next')}>
                        <ChevronRight size={20} aria-hidden="true" />
                    </button>
                )}
                <p className="image-viewer-caption" dir="auto">
                    <bdi>{image.name}</bdi>
                    {many && <> · <bdi dir="ltr">{t('imageViewer.count', { n: index + 1, total: images.length })}</bdi></>}
                </p>
            </div>
        </div>,
        document.body,
    );
}
