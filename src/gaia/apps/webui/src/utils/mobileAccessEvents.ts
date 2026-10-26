// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

/**
 * Phone access is owned by the app shell (it holds the tunnel state and the
 * QR-code dialog). The session panel's toggle asks it to start or stop, and
 * hears back when the state changes, so both always agree.
 */
const REQUEST = 'gaia:mobile-access-request';
const STATE = 'gaia:mobile-access-state';

export function requestMobileAccess(on: boolean): void {
    window.dispatchEvent(new CustomEvent<boolean>(REQUEST, { detail: on }));
}

export function announceMobileAccess(active: boolean): void {
    window.dispatchEvent(new CustomEvent<boolean>(STATE, { detail: active }));
}

function useWindowEvent(name: string, handler: (value: boolean) => void): void {
    const cb = useRef(handler);
    cb.current = handler;
    useEffect(() => {
        const listener = (e: Event) => cb.current(Boolean((e as CustomEvent<boolean>).detail));
        window.addEventListener(name, listener);
        return () => window.removeEventListener(name, listener);
    }, [name]);
}

export const useMobileAccessRequests = (handler: (on: boolean) => void) => useWindowEvent(REQUEST, handler);
export const useMobileAccessState = (handler: (active: boolean) => void) => useWindowEvent(STATE, handler);
