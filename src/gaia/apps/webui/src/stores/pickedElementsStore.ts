// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Elements the user picked in the browser pane, as chips in the composer
 * until the next message takes them. The pane belongs to the window, not to
 * one chat, so the chips do too. Each chip's crop uploads as soon as it
 * arrives and travels like any image attachment.
 */

import { create } from 'zustand';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { PageElementWire, PickEvent, PickedElement } from '../../services/browser-pane/contract';

export type ChipPart = 'html' | 'styles' | 'screenshot';

/** The most one message carries; the UI server refuses more (`MAX_ELEMENTS` in gaia/browser/picked.py). */
export const MAX_PICKED_ELEMENTS = 8;

export interface ElementChipState {
    element: PickedElement;
    /** The picking run that made it; a later event of that run replaces the run's chips. */
    run: number;
    include: Record<ChipPart, boolean>;
    cropUrl: string | null;
    uploading: boolean;
    error: string | null;
}

interface PickedElementsStore {
    chips: ElementChipState[];
    /** Refs the user removed; a later event of the run that picked them must not bring them back. */
    dismissed: string[];
    applyPick: (event: PickEvent) => void;
    toggle: (ref: string, part: ChipPart) => void;
    remove: (ref: string) => void;
    /** The chips as the next chat request carries them; clears the composer. */
    take: () => PageElementWire[] | undefined;
}

function cropFile(element: PickedElement): File {
    const bytes = Uint8Array.from(atob(element.crop as string), (c) => c.charCodeAt(0));
    return new File([bytes], `picked-${element.ref}.png`, { type: 'image/png' });
}

export function toWire(chip: ElementChipState): PageElementWire {
    const { crop: _crop, outerHTML, styles, ...rest } = chip.element;
    return {
        ...rest,
        outerHTML: chip.include.html ? outerHTML : null,
        styles: chip.include.styles ? styles : null,
        cropUrl: chip.include.screenshot ? chip.cropUrl : null,
    };
}

export const usePickedElementsStore = create<PickedElementsStore>((set, get) => {
    const update = (ref: string, change: Partial<ElementChipState>) =>
        set((s) => ({ chips: s.chips.map((c) => (c.element.ref === ref ? { ...c, ...change } : c)) }));

    const upload = (element: PickedElement) => {
        api.uploadFile(cropFile(element))
            .then((res) => update(element.ref, { uploading: false, cropUrl: res.url }))
            .catch((err) => {
                log.chat.error(`Could not upload the picture of picked element ${element.ref}`, err);
                update(element.ref, { uploading: false, error: err instanceof Error ? err.message : String(err) });
            });
    };

    return {
        chips: [],
        dismissed: [],
        applyPick: ({ run, elements }) => {
            const before = new Map(get().chips.map((c) => [c.element.ref, c]));
            const fresh: PickedElement[] = [];
            const mine = elements.filter((e) => !get().dismissed.includes(e.ref)).map((element): ElementChipState => {
                const kept = before.get(element.ref);
                if (kept) return { ...kept, element, run };
                fresh.push(element);
                return {
                    element,
                    run,
                    // Off by default: a crop makes the agent look at it, which costs minutes per reply.
                    include: { html: true, styles: true, screenshot: false },
                    cropUrl: null,
                    uploading: element.crop !== null,
                    error: null,
                };
            });
            set((s) => ({ chips: [...s.chips.filter((c) => c.run !== run), ...mine] }));
            fresh.filter((e) => e.crop !== null).forEach(upload);
        },
        toggle: (ref, part) =>
            set((s) => ({
                chips: s.chips.map((c) =>
                    c.element.ref === ref ? { ...c, include: { ...c.include, [part]: !c.include[part] } } : c,
                ),
            })),
        remove: (ref) =>
            set((s) => ({ chips: s.chips.filter((c) => c.element.ref !== ref), dismissed: [...s.dismissed, ref] })),
        take: () => {
            const { chips } = get();
            if (chips.length === 0) return undefined;
            set({ chips: [] });
            return chips.map(toWire);
        },
    };
});
