// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Elements picked in the browser pane, shown as chips: editable above the
 * composer, read-only in the bubble of the message that sent them.
 */

import { MousePointerClick, X } from 'lucide-react';
import { MAX_PICKED_ELEMENTS, usePickedElementsStore, type ChipPart, type ElementChipState } from '../../stores/pickedElementsStore';
import type { PageElementWire } from '../../../services/browser-pane/contract';
import './ElementChip.css';

/** `button.primary "Save"`, as the outline labelled it. */
export function chipLabel(el: { label: string; name: string }): string {
    const name = el.name.length > 40 ? `${el.name.slice(0, 39)}…` : el.name;
    return name ? `${el.label} "${name}"` : el.label;
}

const PARTS: { part: ChipPart; label: string }[] = [
    { part: 'html', label: 'HTML' },
    { part: 'styles', label: 'Styles' },
    { part: 'screenshot', label: 'Screenshot' },
];

function ElementChip({ chip }: { chip: ElementChipState }) {
    const { element: el } = chip;
    const toggle = usePickedElementsStore((s) => s.toggle);
    const remove = usePickedElementsStore((s) => s.remove);
    const label = chipLabel(el);
    return (
        <li className={`element-chip${chip.error ? ' has-error' : ''}`} data-ref={el.ref}>
            {el.crop
                ? <img className="element-chip-thumb" src={`data:image/png;base64,${el.crop}`} alt="" />
                : <MousePointerClick size={14} aria-hidden="true" />}
            <span className="element-chip-text" title={`${el.ref}: ${el.selector}\n${el.url}`}>
                <span className="element-chip-label">{label}</span>
                {el.component && <span className="element-chip-component">&lt;{el.component.name}&gt;</span>}
            </span>
            <span className="element-chip-parts" role="group" aria-label={`What to send for ${label}`}>
                {PARTS.map(({ part, label: partLabel }) => {
                    const unavailable = part === 'screenshot' && (el.crop === null || chip.error !== null);
                    return (
                        <button
                            key={part}
                            type="button"
                            className="element-chip-part"
                            aria-pressed={chip.include[part] && !unavailable}
                            disabled={unavailable}
                            title={unavailable ? 'No screenshot of this element' : `${chip.include[part] ? 'Leave out' : 'Send'} its ${partLabel.toLowerCase()}`}
                            onClick={() => toggle(el.ref, part)}
                        >
                            {partLabel}
                        </button>
                    );
                })}
            </span>
            {chip.uploading && <span className="element-chip-state">Uploading…</span>}
            {chip.error && <span className="element-chip-state" role="alert">Screenshot did not upload</span>}
            <button
                type="button"
                className="composer-icon-btn"
                onClick={() => remove(el.ref)}
                aria-label={`Remove ${label}`}
            >
                <X size={12} />
            </button>
        </li>
    );
}

/** The composer's chips, with the reason the message can't go out yet when there is one. */
export function ElementChips() {
    const chips = usePickedElementsStore((s) => s.chips);
    if (chips.length === 0) return null;
    return (
        <>
            <ul className="element-chips" aria-label="Picked elements">
                {chips.map((chip) => <ElementChip key={chip.element.ref} chip={chip} />)}
            </ul>
            {chips.length > MAX_PICKED_ELEMENTS && (
                <p className="composer-limit-error" role="alert">
                    A message carries at most {MAX_PICKED_ELEMENTS} picked elements. Remove {chips.length - MAX_PICKED_ELEMENTS}.
                </p>
            )}
        </>
    );
}

/** Why the picked elements stop a send right now, or null. */
export function useElementChipsBlock(streaming: boolean): string | null {
    return usePickedElementsStore((s) => {
        if (s.chips.length === 0) return null;
        if (s.chips.length > MAX_PICKED_ELEMENTS) return `At most ${MAX_PICKED_ELEMENTS} picked elements`;
        if (s.chips.some((c) => c.uploading)) return 'A picked element is still uploading';
        if (streaming) return 'Picked elements send once GAIA finishes this reply';
        return null;
    });
}

/** The chips a sent message carried, read-only. */
export function SentElementChips({ elements }: { elements: PageElementWire[] }) {
    return (
        <ul className="element-chips is-sent" aria-label="Picked elements">
            {elements.map((el) => (
                <li key={el.ref} className="element-chip" data-ref={el.ref} title={`${el.ref}: ${el.selector}\n${el.url}`}>
                    <MousePointerClick size={14} aria-hidden="true" />
                    <span className="element-chip-text">
                        <span className="element-chip-label">{chipLabel(el)}</span>
                        {el.component && <span className="element-chip-component">&lt;{el.component.name}&gt;</span>}
                    </span>
                </li>
            ))}
        </ul>
    );
}
