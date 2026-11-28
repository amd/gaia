// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useT } from '../i18n/useT';
import './SkillMention.css';

const GAP = 6;
const EDGE = 8;

export interface TipAnchor {
    left: number;
    right: number;
    top: number;
    bottom: number;
}

/**
 * Where a tip of *size* goes for a word at *anchor*: above it, or below when
 * the top is short of room, lined up with the word's start edge.
 */
export function placeSkillTip(
    anchor: TipAnchor,
    size: { width: number; height: number },
    viewport: { width: number; height: number },
    rtl: boolean,
) {
    const fitsAbove = anchor.top - GAP - size.height >= EDGE;
    const top = fitsAbove ? anchor.top - GAP - size.height : Math.min(anchor.bottom + GAP, viewport.height - EDGE - size.height);
    const start = rtl ? anchor.right - size.width : anchor.left;
    const left = Math.min(Math.max(EDGE, start), Math.max(EDGE, viewport.width - EDGE - size.width));
    return { left, top };
}

/** Closes a tip when anything scrolls: it was placed beside a word that has now moved. */
export function useCloseOnScroll(open: boolean, close: () => void): void {
    useEffect(() => {
        if (!open) return;
        window.addEventListener('scroll', close, true);
        return () => window.removeEventListener('scroll', close, true);
    }, [open, close]);
}

/** A skill's name and description beside the `/name` word it belongs to; on document.body, so no box clips it. */
export function SkillTip({ id, anchor, name, description }: { id?: string; anchor: TipAnchor; name: string; description: string }) {
    const t = useT();
    const ref = useRef<HTMLDivElement>(null);
    const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

    useLayoutEffect(() => {
        const box = ref.current?.getBoundingClientRect();
        if (!box) return;
        const rtl = getComputedStyle(document.documentElement).direction === 'rtl';
        setPos(placeSkillTip(anchor, box, { width: window.innerWidth, height: window.innerHeight }, rtl));
    }, [anchor.left, anchor.top, anchor.bottom, anchor.right]); // eslint-disable-line react-hooks/exhaustive-deps

    return createPortal(
        <div
            ref={ref}
            id={id}
            role="tooltip"
            className="skill-tip"
            style={pos ? { left: pos.left, top: pos.top } : { visibility: 'hidden', left: 0, top: 0 }}
        >
            <span className="skill-tip-head">
                <span className="skill-tip-kind">{t('skillMention.kind')}</span>
                <span className="skill-tip-name" dir="ltr">/{name}</span>
            </span>
            <span className="skill-tip-desc" dir="auto">{description}</span>
        </div>,
        document.body,
    );
}

/** The skill command in a sent message, marked like the composer marks it, with its description on hover or focus. */
export function SkillMention({ name, description, children }: { name: string; description: string; children: ReactNode }) {
    const id = useId();
    const ref = useRef<HTMLSpanElement>(null);
    const [anchor, setAnchor] = useState<TipAnchor | null>(null);
    const show = () => {
        const box = ref.current?.getBoundingClientRect();
        if (box) setAnchor({ left: box.left, right: box.right, top: box.top, bottom: box.bottom });
    };
    const hide = useCallback(() => setAnchor(null), []);
    useCloseOnScroll(anchor !== null, hide);
    return (
        <>
            <span
                ref={ref}
                className="skill-word skill-mention"
                // The message's own hover title (when it was sent) would cover the tip.
                title=""
                tabIndex={0}
                aria-describedby={anchor ? id : undefined}
                onMouseEnter={show}
                onMouseLeave={hide}
                onFocus={show}
                onBlur={hide}
            >
                {children}
            </span>
            {anchor && <SkillTip id={id} anchor={anchor} name={name} description={description} />}
        </>
    );
}
