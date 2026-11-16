// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Hand-written: the web UI defines no spacing tokens, so the phone scale lives here.
export const space = {
    none: 0,
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
    xxl: 32,
    // The side margin between content and the screen edge.
    gutter: 16,
} as const;

export type Space = keyof typeof space;
