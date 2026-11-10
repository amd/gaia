// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Elements picked in the browser pane, shown as chips: editable above the
 * composer, read-only in the bubble of the message that sent them.
 */

import { MousePointerClick, X } from 'lucide-react';
import { MAX_PICKED_ELEMENTS, usePickedElementsStore, type ChipPart, type ElementChipState } from '../../stores/pickedElementsStore';
import type { PageElementWire } from '../../../services/browser-pane/contract';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';
import './ElementChip.css';

/** `button.primary "Save"`, as the outline labelled it. */
export function chipLabel(el: { label: string; name: string }): string {
    const name = el.name.length > 40 ? `${el.name.slice(0, 39)}…` : el.name;
    return name ? `${el.label} "${name}"` : el.label;
}

const PARTS: { part: ChipPart; label: MessageKey; send: MessageKey; leaveOut: MessageKey }[] = [
    { part: 'html', label: 'browserPane.chip.part.html', send: 'browserPane.chip.send.html', leaveOut: 'browserPane.chip.leaveOut.html' },
    { part: 'styles', label: 'browserPane.chip.part.styles', send: 'browserPane.chip.send.styles', leaveOut: 'browserPane.chip.leaveOut.styles' },
    { part: 'screenshot', label: 'browserPane.chip.part.screenshot', send: 'browserPane.chip.send.screenshot', leaveOut: 'browserPane.chip.leaveOut.screenshot' },
];

function ElementChip({ chip }: { chip: ElementChipState }) {
    const { element: el } = chip;
    const toggle = usePickedElementsStore((s) => s.toggle);
    const remove = usePickedElementsStore((s) => s.remove);
    const t = useT();
    const label = chipLabel(el);
    return (
        <li className={`element-chip${chip.error ? ' has-error' : ''}`} data-ref={el.ref}>
            {el.crop
                ? <img className="element-chip-thumb" src={`data:image/png;base64,${el.crop}`} alt="" />
                : <MousePointerClick size={14} aria-hidden="true" />}
            <span className="element-chip-text" title={`${el.ref}: ${el.selector}\n${el.url}`}>
                <span className="element-chip-label">{label}</span>
                {el.component && <span className="element-chip-component">{'<'}{el.component.name}{'>'}</span>}
            </span>
            <span className="element-chip-parts" role="group" aria-label={t('browserPane.chip.whatToSend', { label })}>
                {PARTS.map(({ part, label: partLabel, send, leaveOut }) => {
                    const unavailable = part === 'screenshot' && (el.crop === null || chip.error !== null);
                    return (
                        <button
                            key={part}
                            type="button"
                            className="element-chip-part"
                            aria-pressed={chip.include[part] && !unavailable}
                            disabled={unavailable}
                            title={unavailable ? t('browserPane.chip.noScreenshot') : t(chip.include[part] ? leaveOut : send)}
                            onClick={() => toggle(el.ref, part)}
                        >
                            {t(partLabel)}
                        </button>
                    );
                })}
            </span>
            {chip.uploading && <span className="element-chip-state">{t('browserPane.chip.uploading')}</span>}
            {chip.error && <span className="element-chip-state" role="alert">{t('browserPane.chip.uploadFailed')}</span>}
            <button
                type="button"
                className="composer-icon-btn"
                onClick={() => remove(el.ref)}
                aria-label={t('browserPane.chip.remove', { label })}
            >
                <X size={12} />
            </button>
        </li>
    );
}

/** The composer's chips, with the reason the message can't go out yet when there is one. */
export function ElementChips() {
    const chips = usePickedElementsStore((s) => s.chips);
    const t = useT();
    if (chips.length === 0) return null;
    return (
        <>
            <ul className="element-chips" aria-label={t('browserPane.chip.list')}>
                {chips.map((chip) => <ElementChip key={chip.element.ref} chip={chip} />)}
            </ul>
            {chips.length > MAX_PICKED_ELEMENTS && (
                <p className="composer-limit-error" role="alert">
                    {t('browserPane.chip.tooMany', { max: MAX_PICKED_ELEMENTS, n: chips.length - MAX_PICKED_ELEMENTS })}
                </p>
            )}
        </>
    );
}

/** Why the picked elements stop a send right now, or null. */
export function useElementChipsBlock(streaming: boolean): string | null {
    const t = useT();
    const block = usePickedElementsStore((s): MessageKey | null => {
        if (s.chips.length === 0) return null;
        if (s.chips.length > MAX_PICKED_ELEMENTS) return 'browserPane.block.tooMany';
        if (s.chips.some((c) => c.uploading)) return 'browserPane.block.uploading';
        if (streaming) return 'browserPane.block.streaming';
        return null;
    });
    return block === null ? null : t(block, { max: MAX_PICKED_ELEMENTS });
}

/** The chips a sent message carried, read-only. */
export function SentElementChips({ elements }: { elements: PageElementWire[] }) {
    const t = useT();
    return (
        <ul className="element-chips is-sent" aria-label={t('browserPane.chip.list')}>
            {elements.map((el) => (
                <li key={el.ref} className="element-chip" data-ref={el.ref} title={`${el.ref}: ${el.selector}\n${el.url}`}>
                    <MousePointerClick size={14} aria-hidden="true" />
                    <span className="element-chip-text">
                        <span className="element-chip-label">{chipLabel(el)}</span>
                        {el.component && <span className="element-chip-component">{'<'}{el.component.name}{'>'}</span>}
                    </span>
                </li>
            ))}
        </ul>
    );
}
