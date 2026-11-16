// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Inter_400Regular } from '@expo-google-fonts/inter/400Regular';
import { Inter_500Medium } from '@expo-google-fonts/inter/500Medium';
import { Inter_600SemiBold } from '@expo-google-fonts/inter/600SemiBold';
import { Inter_700Bold } from '@expo-google-fonts/inter/700Bold';
import { JetBrainsMono_400Regular } from '@expo-google-fonts/jetbrains-mono/400Regular';
import { JetBrainsMono_500Medium } from '@expo-google-fonts/jetbrains-mono/500Medium';
import { Fraunces_300Light } from '@expo-google-fonts/fraunces/300Light';
import { faces } from '../tokens/generated';

export type Weight = 'regular' | 'medium' | 'semibold' | 'bold';

const key = (face: string) => face.replace(/\s+/g, '');
const SANS = key(faces.sans);
const MONO = key(faces.mono);
const GREETING = key(faces.greeting);

// One family name per weight: Android can't pick a weight out of one family.
export const fontFiles = {
    [`${SANS}_400`]: Inter_400Regular,
    [`${SANS}_500`]: Inter_500Medium,
    [`${SANS}_600`]: Inter_600SemiBold,
    [`${SANS}_700`]: Inter_700Bold,
    [`${MONO}_400`]: JetBrainsMono_400Regular,
    [`${MONO}_500`]: JetBrainsMono_500Medium,
    [`${GREETING}_300`]: Fraunces_300Light,
};

const SANS_WEIGHT: Record<Weight, number> = { regular: 400, medium: 500, semibold: 600, bold: 700 };

export function fontFamily(opts: { weight: Weight; mono: boolean; display: boolean }): string {
    if (opts.display) return `${GREETING}_300`;
    if (opts.mono) return `${MONO}_${opts.weight === 'regular' ? 400 : 500}`;
    return `${SANS}_${SANS_WEIGHT[opts.weight]}`;
}
