// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export interface EyesSheet {
    width: number;
    height: number;
    pixels: Uint8Array;
    meta: Record<string, { row: number; frames: number; fps: number; loop: boolean; pose: number }>;
    variants: Record<string, string[]>;
}

export declare const SHEET_PATH: string;
export declare const META_PATH: string;
export declare const REGENERATE_HINT: string;
export declare const CELL: number;
export declare function buildSheet(): EyesSheet;
export declare function encodePng(sheet: Pick<EyesSheet, 'width' | 'height' | 'pixels'>): Buffer;
export declare function decodePng(png: Buffer): Pick<EyesSheet, 'width' | 'height' | 'pixels'>;
export declare function metaSource(sheet: EyesSheet): string;
