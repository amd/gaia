// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { View } from 'react-native';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated';
import { motion } from '../tokens/generated';
import { useTheme } from '../theme/ThemeProvider';

const BannerContext = createContext<ReactNode>(null);

/** Gives every `Screen` below it the same banner, unless a screen passes its own `banner`. */
export function ScreenBannerProvider({ banner, children }: { banner: ReactNode; children?: ReactNode }) {
    return <BannerContext.Provider value={banner}>{children}</BannerContext.Provider>;
}

export function useScreenBanner(): ReactNode {
    return useContext(BannerContext);
}

/** The banner row above a `Screen`'s header: it measures what the banner renders, so an empty banner takes no space. */
export function ScreenBannerSlot({ banner }: { banner: ReactNode }) {
    const { space } = useTheme();
    const reduced = useReducedMotion();
    const [height, setHeight] = useState(0);
    const open = useSharedValue(0);
    const size = useSharedValue(0);
    const target = height > 0 ? height + space.sm : 0;

    useEffect(() => {
        const config = { duration: reduced ? motion.duration.Fast : motion.duration.standard, easing: Easing.bezier(...motion.easing.standard) };
        open.value = withTiming(height > 0 ? 1 : 0, config);
        size.value = withTiming(target, config);
    }, [height, target, reduced, open, size]);

    const style = useAnimatedStyle(() => ({
        height: size.value,
        opacity: open.value,
        transform: reduced ? [] : [{ translateY: (open.value - 1) * 12 }],
    }));

    // The banner sits absolutely so its natural height is measurable while the wrapper animates.
    return (
        <Animated.View style={[{ overflow: 'hidden' }, style]} pointerEvents={height > 0 ? 'auto' : 'none'}>
            <View style={{ position: 'absolute', top: 0, start: space.lg, end: space.lg }} onLayout={(e) => setHeight(e.nativeEvent.layout.height)}>
                {banner}
            </View>
        </Animated.View>
    );
}
