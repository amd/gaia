// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

/** Mark an inline field or menu whose Escape must not reach the page-wide handlers. */
export const CLAIMS_ESCAPE = { 'data-claims-escape': '' } as const;

/** An open overlay. `close` is absent when the overlay handles Escape itself. */
interface Layer { close?: () => void }

const layers: Layer[] = [];
const layerListeners = new Set<() => void>();

/** Hear every overlay open and close (useAppOverlayOpen). Returns its unsubscribe. */
export function subscribeLayers(listener: () => void): () => void {
    layerListeners.add(listener);
    return () => layerListeners.delete(listener);
}

export function openLayerCount(): number {
    return layers.length;
}

function closeTopLayer(e: KeyboardEvent): void {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    const top = layers[layers.length - 1];
    if (!top) return;
    e.preventDefault();
    top.close?.();
}

function pushLayer(layer: Layer): () => void {
    if (layers.length === 0) document.addEventListener('keydown', closeTopLayer);
    layers.push(layer);
    layerListeners.forEach((l) => l());
    return () => {
        layers.splice(layers.indexOf(layer), 1);
        if (layers.length === 0) document.removeEventListener('keydown', closeTopLayer);
        layerListeners.forEach((l) => l());
    };
}

/**
 * True when Escape belongs to something other than the page: an open dialog,
 * menu or popover, a field marked with CLAIMS_ESCAPE, or a handler that already
 * called preventDefault. Stopping the reply and denying a tool both check this,
 * so they no longer depend on which listener happens to run first.
 */
export function isEscapeClaimed(e: KeyboardEvent): boolean {
    if (e.defaultPrevented || layers.length > 0) return true;
    const target = e.target;
    return target instanceof Element && target.closest('[data-claims-escape]') !== null;
}

/**
 * Register an overlay that handles Escape itself, so the key doesn't also stop
 * the reply. Pass `true` only while it is on screen: an open layer claims every Escape.
 */
export function useEscapeLayer(isOpen: boolean): void {
    useEffect(() => (isOpen ? pushLayer({}) : undefined), [isOpen]);
}

/**
 * Close a modal on Escape, without the key also stopping a streaming reply.
 * Only the most recently opened overlay closes, so Esc in a menu inside
 * Settings closes the menu and leaves Settings open. While `enabled` is false
 * the overlay still claims Escape but stays open.
 */
export function useEscapeToClose(isOpen: boolean, onClose: () => void, enabled = true): void {
    const closeRef = useRef(onClose);
    const enabledRef = useRef(enabled);
    useEffect(() => {
        closeRef.current = onClose;
        enabledRef.current = enabled;
    });
    useEffect(() => {
        if (!isOpen) return;
        return pushLayer({ close: () => { if (enabledRef.current) closeRef.current(); } });
    }, [isOpen]);
}
