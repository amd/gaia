// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { copyText } from '../utils/clipboard';
import { log } from '../utils/logger';

export type CopyState = 'idle' | 'copied' | 'failed';

const COPIED_MS = 2000;
const FAILED_MS = 4000;

/** A copy button's state: "copied" only once the clipboard took the text, "failed" when it refused. */
export function useCopyFeedback(): [CopyState, (text: string) => void] {
    const [state, setState] = useState<CopyState>('idle');
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, []);

    const show = useCallback((next: CopyState, ms: number) => {
        if (!mountedRef.current) return;
        setState(next);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setState('idle'), ms);
    }, []);

    const copy = useCallback((text: string) => {
        copyText(text).then(
            () => show('copied', COPIED_MS),
            (err: unknown) => {
                log.ui.warn('Copy to clipboard failed', err);
                show('failed', FAILED_MS);
            },
        );
    }, [show]);

    return [state, copy];
}
