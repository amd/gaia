// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useRef, useState } from 'react';
import { useT } from '../i18n/useT';
import { ImageViewer, type ViewerImage } from './ImageViewer';
import './ImageThumbs.css';

/** The images a message carries, as small tiles that open full size. */
export function ImageThumbs({ images }: { images: readonly ViewerImage[] }) {
    const t = useT();
    const [open, setOpen] = useState<number | null>(null);
    const tiles = useRef<(HTMLButtonElement | null)[]>([]);

    const close = useCallback(() => {
        // The viewer may have stepped to another image: focus goes back to the one now shown.
        const at = open;
        setOpen(null);
        if (at !== null) requestAnimationFrame(() => tiles.current[at]?.focus());
    }, [open]);

    if (images.length === 0) return null;
    return (
        <>
            <ul className="image-thumbs" aria-label={t('msg.images')}>
                {images.map((image, i) => (
                    <li key={`${image.src}-${i}`}>
                        <button
                            type="button"
                            ref={(el) => { tiles.current[i] = el; }}
                            className="image-thumb"
                            onClick={() => setOpen(i)}
                            aria-label={t('msg.imageOpen', { name: image.name })}
                            title={image.name}
                        >
                            <img src={image.src} alt="" loading="lazy" />
                        </button>
                    </li>
                ))}
            </ul>
            {open !== null && <ImageViewer images={images} index={open} onIndex={setOpen} onClose={close} />}
        </>
    );
}
