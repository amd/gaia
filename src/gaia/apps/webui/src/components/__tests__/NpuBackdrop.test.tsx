// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createRef } from 'react';
import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NpuBackdrop } from '../NpuBackdrop';
import type { TileActivity } from '../npuTiles';

function mockReducedMotion(reduce: boolean): void {
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            matches: reduce && query === '(prefers-reduced-motion: reduce)',
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }),
    });
}

function setHidden(hidden: boolean): void {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    document.dispatchEvent(new Event('visibilitychange'));
}

const contentRef = createRef<HTMLDivElement>();
const composerRef = createRef<HTMLDivElement>();

function view(activity: TileActivity = 'idle', pulse = 0) {
    return (
        <main>
            <NpuBackdrop activity={activity} pulse={pulse} docked={false} contentRef={contentRef} composerRef={composerRef} />
            <div ref={contentRef}><div ref={composerRef}><textarea /></div></div>
        </main>
    );
}

let raf: ReturnType<typeof vi.spyOn>;
let cancel: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
    raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
});

afterEach(() => {
    vi.restoreAllMocks();
    setHidden(false);
    Reflect.deleteProperty(window, 'matchMedia');
});

describe('NpuBackdrop', () => {
    it('sits behind the content as a decorative canvas screen readers skip', () => {
        mockReducedMotion(false);
        const { container } = render(view());
        const canvas = container.querySelector('canvas.npu-backdrop');
        expect(canvas).toHaveAttribute('aria-hidden', 'true');
        expect(canvas?.parentElement?.tagName).toBe('MAIN');
    });

    it('animates when motion is allowed and stops on unmount', () => {
        mockReducedMotion(false);
        const { unmount } = render(view());
        expect(raf).toHaveBeenCalled();
        unmount();
        expect(cancel).toHaveBeenCalled();
    });

    it('holds a still frame under reduced motion', () => {
        mockReducedMotion(true);
        render(view());
        expect(raf).not.toHaveBeenCalled();
    });

    it('pauses while the window is hidden and resumes when it comes back', () => {
        mockReducedMotion(false);
        render(view());
        raf.mockClear();
        setHidden(true);
        expect(cancel).toHaveBeenCalled();
        setHidden(false);
        expect(raf).toHaveBeenCalled();
    });

    it('keeps rendering through every state the new chat passes through', () => {
        mockReducedMotion(false);
        const { rerender } = render(view('idle'));
        for (const [activity, pulse] of [['loading', 1], ['download', 2], ['sending', 3], ['idle', 4]] as const) {
            rerender(view(activity, pulse));
        }
        expect(document.querySelector('canvas.npu-backdrop')).toBeInTheDocument();
    });
});
