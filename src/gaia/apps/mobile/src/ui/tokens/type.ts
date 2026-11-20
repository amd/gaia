// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fontSizes } from './generated';

// The phone scale: each upstream role plus 2 pt, landing on Apple's text styles.
// `display` is the greeting face and exists only on phones.
export const typeScale = {
    caption: fontSizes.caption + 2,
    label: fontSizes.label + 2,
    ui: fontSizes.ui + 2,
    body: fontSizes.body + 2,
    prose: fontSizes.prose + 2,
    title: fontSizes.title + 2,
    display: 28,
} as const;

export type TypeRole = keyof typeof typeScale;
