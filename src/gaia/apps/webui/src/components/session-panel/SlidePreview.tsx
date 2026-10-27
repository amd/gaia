// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo, useState } from 'react';
import { FileText, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useSlideDraftStore, type SlideDraft } from '../../stores/slideDraftStore';
import { parseSlides, type DraftSlide } from '../../utils/slideMarkdown';
import { PanelSection } from './PanelSection';

const STATUS: Record<SlideDraft['status'], string> = {
    preparing: 'Getting ready to write the slides…',
    writing: 'Writing the slides…',
    building: 'Building the PowerPoint file…',
    retrying: 'Fixing a problem and trying again…',
    done: 'Ready',
    failed: 'The deck could not be built',
};

function Bullets({ items }: { items: string[] }) {
    if (!items.length) return null;
    return (
        <ul className="sd-bullets">
            {items.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
    );
}

function SlideBody({ slide }: { slide: DraftSlide }) {
    switch (slide.layout) {
        case 'cover':
            return (
                <div className="sd-cover">
                    <div className="sd-cover-title">{slide.title}</div>
                    {slide.subtitle && <div className="sd-cover-sub">{slide.subtitle}</div>}
                </div>
            );
        case 'process':
        case 'boxes':
            return (
                <>
                    <div className={`sd-stages sd-${slide.layout}`} style={{ gridTemplateColumns: `repeat(${Math.max(slide.items.length, 1)}, 1fr)` }}>
                        {slide.items.map((it, i) => (
                            <div className="sd-stage" key={i}>
                                <div className="sd-stage-head">{slide.layout === 'process' ? `${i + 1}. ` : ''}{it.title}</div>
                                <div className="sd-stage-card"><Bullets items={it.bullets} /></div>
                            </div>
                        ))}
                    </div>
                    {slide.axis && <div className="sd-axis">{slide.axis}</div>}
                </>
            );
        case 'columns':
            return (
                <div className="sd-columns">
                    {slide.items.map((it, i) => (
                        <div key={i}>
                            <div className="sd-col-head">{it.title}</div>
                            <Bullets items={it.bullets} />
                        </div>
                    ))}
                </div>
            );
        case 'table':
            return (
                <table className="sd-table">
                    <tbody>
                        {(slide.rows ?? []).map((r, i) => (
                            <tr key={i}>{r.map((c, j) => (i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>))}</tr>
                        ))}
                    </tbody>
                </table>
            );
        case 'chart':
            return <div className="sd-chart">{slide.chart} chart</div>;
        default:
            return <Bullets items={slide.bullets} />;
    }
}

function Slide({ slide, n }: { slide: DraftSlide; n: number }) {
    return (
        <div className="sd-slide" aria-label={`Slide ${n}: ${slide.title}`}>
            {slide.layout !== 'cover' && <div className="sd-title">{slide.title}</div>}
            <div className="sd-body"><SlideBody slide={slide} /></div>
            {slide.source && <div className="sd-source">Source: {slide.source}</div>}
        </div>
    );
}

/** The deck this chat is making, drawn from its Markdown while it is written. */
export function SlidePreview({ sessionId }: { sessionId: string }) {
    const draft = useSlideDraftStore((s) => s.drafts[sessionId]);
    // A rewrite starts from nothing; keep the last slide up until it catches up.
    const catchingUp = !!draft?.previous && draft.status === 'writing' && draft.markdown.length < draft.previous.length;
    const shown = catchingUp ? draft.previous ?? '' : draft?.markdown ?? '';
    const slides = useMemo(() => parseSlides(shown), [shown]);
    const [openError, setOpenError] = useState<string | null>(null);
    if (!draft) return null;
    const working = draft.status !== 'done' && draft.status !== 'failed';
    const name = draft.path?.split(/[\\/]/).pop();
    return (
        <PanelSection id="slide" title="Slide preview" summary={working ? 'Live' : undefined}>
            <div className="sd-preview">
                <div className={`sd-status${draft.status === 'failed' ? ' is-error' : ''}`} role="status">
                    {working && <Loader2 size={12} className="sd-spin" aria-hidden="true" />}
                    {catchingUp ? 'Revising the slides…' : STATUS[draft.status]}
                    {draft.status === 'failed' && draft.error ? `: ${draft.error}` : ''}
                </div>
                {slides.length === 0 && working && <div className="sd-slide sd-empty" aria-hidden="true" />}
                {slides.map((s, i) => <Slide key={i} slide={s} n={i + 1} />)}
                {draft.status === 'done' && draft.path && (
                    <button
                        type="button"
                        className="sp-action sd-open"
                        onClick={() => api.openFileInApp(draft.path as string).catch((err) => setOpenError(String(err?.message ?? err)))}
                    >
                        <FileText size={12} aria-hidden="true" /> Open {name}
                    </button>
                )}
                {openError && <div className="sd-status is-error">Couldn't open {name}: {openError}</div>}
            </div>
        </PanelSection>
    );
}
