// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Animated, { FadeIn, FadeInDown, FadeOut, FadeOutDown, useReducedMotion } from 'react-native-reanimated';

import { useT } from '@/i18n/useT';
import { Floating, IconButton } from '@/ui';
import { ArrowDown } from '@/ui/icons';

/** Slides and fades in and out; under reduced motion it only fades. */
export function JumpButton({ visible, onPress }: { visible: boolean; onPress: () => void }) {
    const t = useT();
    const reduced = useReducedMotion();
    return (
        <Floating>
            {visible ? (
                <Animated.View entering={reduced ? FadeIn : FadeInDown} exiting={reduced ? FadeOut : FadeOutDown}>
                    <IconButton icon={ArrowDown} label={t('chat.scrollBottom')} variant="filled" onPress={onPress} />
                </Animated.View>
            ) : null}
        </Floating>
    );
}
