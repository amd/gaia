// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useSavedDraft } from '../useSavedDraft';

afterEach(() => localStorage.clear());

describe('useSavedDraft', () => {
    it('keeps typed text across a remount (an app restart)', () => {
        const first = renderHook(() => useSavedDraft('chat:a'));
        act(() => first.result.current[1]('half a question'));
        first.unmount();

        const again = renderHook(() => useSavedDraft('chat:a'));
        expect(again.result.current[0]).toBe('half a question');
    });

    it('forgets the draft once it is cleared (sent)', () => {
        const { result } = renderHook(() => useSavedDraft('chat:a'));
        act(() => result.current[1]('hi'));
        act(() => result.current[1](''));
        expect(localStorage.getItem('gaia:draft:chat:a')).toBeNull();
    });

    it('keeps each chat its own draft when switching', () => {
        const { result, rerender } = renderHook(({ k }) => useSavedDraft(k), { initialProps: { k: 'chat:a' } });
        act(() => result.current[1]('for a'));
        rerender({ k: 'chat:b' });
        expect(result.current[0]).toBe('');
        act(() => result.current[1]('for b'));
        rerender({ k: 'chat:a' });
        expect(result.current[0]).toBe('for a');
        expect(localStorage.getItem('gaia:draft:chat:b')).toBe('for b');
    });

    it("the user's saved words win over a suggested prefill", () => {
        localStorage.setItem('gaia:draft:new-chat', 'my half-typed question');
        const { result } = renderHook(() => useSavedDraft('new-chat', 'suggested prompt'));
        expect(result.current[0]).toBe('my half-typed question');
        localStorage.clear();
        const fresh = renderHook(() => useSavedDraft('new-chat', 'suggested prompt'));
        expect(fresh.result.current[0]).toBe('suggested prompt');
    });

    it('a private chat (null key) never writes its draft to disk', () => {
        const { result } = renderHook(() => useSavedDraft(null));
        act(() => result.current[1]('secret'));
        expect(result.current[0]).toBe('secret');
        expect(Object.keys(localStorage).filter((k) => k.startsWith('gaia:draft:'))).toEqual([]);
    });
});
