// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, type ReactNode } from 'react';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { motion } from '../tokens/generated';

// Fades its child in and out; holds still under reduced motion.
export function Pulse({ children, duration = motion.duration.Pulse, low = 0.4 }: { children: ReactNode; duration?: number; low?: number }) {
    const reduced = useReducedMotion();
    const v = useSharedValue(1);
    useEffect(() => {
        if (reduced) {
            v.value = 1;
            return;
        }
        v.value = withRepeat(withTiming(low, { duration: duration / 2, easing: Easing.inOut(Easing.ease) }), -1, true);
    }, [reduced, duration, low, v]);
    const style = useAnimatedStyle(() => ({ opacity: v.value }));
    return <Animated.View style={style}>{children}</Animated.View>;
}
