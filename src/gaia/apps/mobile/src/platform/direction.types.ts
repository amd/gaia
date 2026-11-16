// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export type Direction = 'ltr' | 'rtl';

/** `picked` is true when the language was chosen explicitly rather than following the phone. */
export type ApplyDirection = (dir: Direction, lang: string, picked: boolean) => void;

/** The direction the screen is laid out in right now (native layout only changes on restart). */
export type LayoutDirection = () => Direction;
