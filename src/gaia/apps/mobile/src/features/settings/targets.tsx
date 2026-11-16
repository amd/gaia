// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentRef, ReactNode, RefObject } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';

import { useTheme } from '@/ui';

import type { SectionId } from './sections';

interface Entry {
    ref: RefObject<ComponentRef<typeof View> | null>;
    onTarget?: () => void;
}

interface TargetsValue {
    section: SectionId;
    register: (name: string, entry: Entry) => () => void;
    flash: { name: string; n: number } | null;
}

const FLASH_MS = 1600;
const SETTLE_MS = 150;

const Ctx = createContext<TargetsValue | null>(null);

export function useSettingsSection(): SectionId {
    const value = useContext(Ctx);
    if (!value) throw new Error('useSettingsSection must be used inside a settings section screen.');
    return value.section;
}

/**
 * Provides the anchors of one section screen. When `target` names a registered anchor, it opens the
 * anchor's parent (`onTarget`), scrolls it into view and flashes it; anchors that mount later (lazy panes) are caught on registration.
 */
export function SettingsTargets({ section, target, scrollRef, children }: {
    section: SectionId;
    target?: string;
    scrollRef: RefObject<ComponentRef<typeof ScrollView> | null>;
    children: ReactNode;
}) {
    const entries = useRef(new Map<string, Entry>());
    const landed = useRef(false);
    const [flash, setFlash] = useState<TargetsValue['flash']>(null);

    useEffect(() => {
        landed.current = false;
    }, [target]);

    const land = useCallback((name: string, entry: Entry) => {
        landed.current = true;
        entry.onTarget?.();
        // Lets the opened parent lay out before measuring.
        setTimeout(() => {
            const scroll = scrollRef.current;
            const node = entry.ref.current;
            const inner = scroll?.getInnerViewRef?.() ?? scroll?.getInnerViewNode?.();
            if (scroll && node && inner) {
                node.measureLayout(inner as never, (_x: number, y: number) => scroll.scrollTo({ y: Math.max(0, y - 16), animated: true }), () => undefined);
            }
            setFlash((f) => ({ name, n: (f?.n ?? 0) + 1 }));
        }, SETTLE_MS);
    }, [scrollRef]);

    const register = useCallback((name: string, entry: Entry) => {
        entries.current.set(name, entry);
        if (target && name === target && !landed.current) land(name, entry);
        return () => {
            if (entries.current.get(name) === entry) entries.current.delete(name);
        };
    }, [land, target]);

    const value = useMemo(() => ({ section, register, flash }), [section, register, flash]);
    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Names a view in this section so `openSettings(section, name)` can land on it. `onTarget` opens its parent Disclosure. */
export function useSettingsTarget(name: string, ref: RefObject<ComponentRef<typeof View> | null>, onTarget?: () => void): void {
    const value = useContext(Ctx);
    const latest = useRef(onTarget);
    useEffect(() => {
        latest.current = onTarget;
    });
    const register = value?.register;
    useEffect(() => register?.(name, { ref, onTarget: () => latest.current?.() }), [register, name, ref]);
}

/** A wrapper that registers its children as an anchor and flashes when landed on. */
export function SettingsAnchor({ name, onTarget, children }: { name: string; onTarget?: () => void; children: ReactNode }) {
    const ref = useRef<ComponentRef<typeof View>>(null);
    const { colors, radius } = useTheme();
    const value = useContext(Ctx);
    useSettingsTarget(name, ref, onTarget);
    const opacity = useSharedValue(0);
    const n = value?.flash?.name === name ? value.flash.n : 0;
    useEffect(() => {
        if (!n) return;
        opacity.value = 1;
        opacity.value = withTiming(0, { duration: FLASH_MS });
    }, [n, opacity]);
    const style = useAnimatedStyle(() => ({ opacity: opacity.value }));
    return (
        <View ref={ref} collapsable={false}>
            {children}
            <Animated.View
                pointerEvents="none"
                style={[StyleSheet.absoluteFill, { backgroundColor: colors.accentDim, borderRadius: radius.md }, style]}
            />
        </View>
    );
}
