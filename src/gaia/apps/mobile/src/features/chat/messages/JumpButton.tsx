// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Animated, { FadeInDown, FadeOut, FadeOutDown, useReducedMotion } from 'react-native-reanimated';

import { useT } from '@/i18n/useT';
import { Floating, IconButton } from '@/ui';
import { ArrowDown } from '@/ui/icons';

/** Slides and fades in and out; under reduced motion it appears at once and fades out. */
export function JumpButton({ visible, onPress }: { visible: boolean; onPress: () => void }) {
    const t = useT();
    // No entering under reduced motion: its single frame can be dropped on Android (Reanimated 4.7.1), leaving the view at opacity 0.
    const reduced = useReducedMotion();
    return (
        <Floating>
            {visible ? (
                <Animated.View entering={reduced ? undefined : FadeInDown} exiting={reduced ? FadeOut : FadeOutDown}>
                    <IconButton icon={ArrowDown} label={t('chat.scrollBottom')} variant="filled" onPress={onPress} />
                </Animated.View>
            ) : null}
        </Floating>
    );
}
