// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { BackHandler, Pressable as RNPressable, ScrollView, StyleSheet, useWindowDimensions, View } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useT } from '@/i18n/useT';
import { IconButton } from '../controls/IconButton';
import { X } from '../icons';
import { Row } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';
import { motion } from '../tokens/generated';
import { a11yVisibility } from '../theme/a11y';
import { sheetStore, type SheetEntry } from './sheetStore';

export const WIDE_BREAKPOINT = 600;
const DISMISS_DISTANCE = 100;
const DISMISS_VELOCITY = 900;

/** Wraps the app: hides it from screen readers while a sheet is open and draws the sheet over it. */
export function SheetHost({ children }: { children: ReactNode }) {
    const state = useSyncExternalStore(sheetStore.subscribe, sheetStore.getState);
    const active = state.active ? state.entries[state.active] : undefined;
    return (
        <View style={{ flex: 1 }}>
            <View
                style={{ flex: 1 }}
                {...a11yVisibility({ accessibilityElementsHidden: !!active, importantForAccessibility: active ? 'no-hide-descendants' : 'auto' })}
            >
                {children}
            </View>
            {active ? <ActiveSheet key={active.id} entry={active} /> : null}
        </View>
    );
}

/**
 * Android back over a sheet: closes it and stops there, so the screen beneath doesn't also pop. A sheet that must be
 * answered keeps back; one already closing lets it through, so a second press is the next step back.
 */
export function backFromSheet(entry: Pick<SheetEntry, 'open' | 'dismissible' | 'onClose'>): boolean {
    if (!entry.open) return false;
    if (entry.dismissible) entry.onClose();
    return true;
}

function ActiveSheet({ entry }: { entry: SheetEntry }) {
    const t = useT();
    const { colors, radius, space, shadows } = useTheme();
    const { width, height } = useWindowDimensions();
    const insets = useSafeAreaInsets();
    const reduced = useReducedMotion();
    const wide = width >= WIDE_BREAKPOINT;
    const progress = useSharedValue(0);
    const drag = useSharedValue(0);
    const latest = useRef(entry);
    useEffect(() => {
        latest.current = entry;
    });

    const dismiss = () => {
        if (latest.current.dismissible && latest.current.open) latest.current.onClose();
    };

    // A sheet opened over this one registers later, so Android asks it first.
    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => backFromSheet(latest.current));
        return () => sub.remove();
    }, []);

    useEffect(() => {
        const ms = reduced ? 0 : entry.open ? motion.duration.standard : motion.duration.Fast;
        const config = { duration: ms, easing: Easing.bezier(...motion.easing.standard) };
        progress.value = withTiming(entry.open ? 1 : 0, config);
        if (entry.open) return;
        const timer = setTimeout(() => sheetStore.finished(entry.id), ms);
        return () => clearTimeout(timer);
    }, [entry.open, entry.id, reduced, progress]);

    const pan = usePanGesture({
        runOnJS: true,
        enabled: entry.dismissible && !wide,
        activeOffsetY: 8,
        onUpdate: (e) => {
            drag.value = Math.max(0, e.translationY);
        },
        onDeactivate: (e) => {
            if (drag.value > DISMISS_DISTANCE || e.velocityY > DISMISS_VELOCITY) dismiss();
            // The sheet slides back unless the parent closed it.
            setTimeout(() => {
                if (latest.current.open) drag.value = withTiming(0, { duration: reduced ? 0 : motion.duration.Fast });
            }, 50);
        },
    });

    const scrimStyle = useAnimatedStyle(() => ({ opacity: progress.value }));
    const panelStyle = useAnimatedStyle(() =>
        wide
            ? { opacity: progress.value, transform: [{ scale: 0.96 + 0.04 * progress.value }] }
            : { transform: [{ translateY: (1 - progress.value) * height + drag.value }] },
    );

    const header = (
        <View>
            {wide ? null : (
                <View style={{ alignItems: 'center', paddingTop: space.sm, paddingBottom: space.xs }}>
                    <View style={{ width: 40, height: 4, borderRadius: radius.full, backgroundColor: colors.border }} />
                </View>
            )}
            {entry.title || (wide && entry.dismissible) ? (
                <Row gap="sm" justify="between">
                    <View style={{ flex: 1, minWidth: 0, paddingHorizontal: space.lg, paddingVertical: space.sm }}>
                        {entry.title ? (
                            <Text role="title" weight="semibold" accessibilityRole="header">
                                {entry.title}
                            </Text>
                        ) : null}
                    </View>
                    {wide && entry.dismissible ? <IconButton icon={X} label={t('sp.closeShort')} variant="ghost" onPress={dismiss} /> : null}
                </Row>
            ) : null}
        </View>
    );

    return (
        <View
            style={StyleSheet.absoluteFill}
            accessibilityViewIsModal
            onAccessibilityEscape={dismiss}
            testID="sheet-host-overlay"
        >
            <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: colors.bgModalOverlay }, scrimStyle]}>
                <RNPressable
                    style={StyleSheet.absoluteFill}
                    onPress={dismiss}
                    accessible={entry.dismissible}
                    accessibilityRole="button"
                    accessibilityLabel={t('sp.closeShort')}
                    testID="sheet-scrim"
                />
            </Animated.View>
            <KeyboardAvoidingView
                behavior="padding"
                pointerEvents="box-none"
                style={{ flex: 1, justifyContent: wide ? 'center' : 'flex-end', alignItems: wide ? 'center' : 'stretch' }}
            >
                <Animated.View
                    style={[
                        {
                            backgroundColor: colors.bgPrimary,
                            maxHeight: height * 0.9,
                            ...(wide
                                ? { width: 520, maxWidth: '100%', borderRadius: radius.xl }
                                : { borderTopStartRadius: radius.xl, borderTopEndRadius: radius.xl, paddingBottom: insets.bottom }),
                            boxShadow: shadows.lg,
                        },
                        panelStyle,
                    ]}
                >
                    {wide ? header : <GestureDetector gesture={pan}>{header}</GestureDetector>}
                    <ScrollView
                        keyboardShouldPersistTaps="handled"
                        style={{ flexGrow: 0 }}
                        contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.lg }}
                    >
                        {entry.children}
                    </ScrollView>
                    {entry.footer ? <View style={{ paddingHorizontal: space.gutter, paddingBottom: space.lg }}>{entry.footer}</View> : null}
                </Animated.View>
            </KeyboardAvoidingView>
        </View>
    );
}
