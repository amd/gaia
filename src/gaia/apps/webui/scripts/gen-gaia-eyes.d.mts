// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export interface EyesSheet {
    width: number;
    height: number;
    pixels: Uint8Array;
    meta: Record<string, { start: number; frames: number; fps: number; loop: boolean; pose: number }>;
    variants: Record<string, string[]>;
}

export declare const SHEET_PATH: string;
export declare const META_PATH: string;
export declare const REGENERATE_HINT: string;
export declare const CELL: number;
export declare const COLUMNS: number;
/** One frame as the generator describes it: two eyes and any loose pixels. */
export interface EyesFrame {
    eyes: Array<{ rows: string[]; dx: number; dy: number; rect?: number[]; shape?: string }>;
    px: number[][];
}
export declare function smooth(frames: EyesFrame[], loop: boolean): EyesFrame[];
export declare function buildSheet(): EyesSheet;
export declare function encodePng(sheet: Pick<EyesSheet, 'width' | 'height' | 'pixels'>): Buffer;
export declare function decodePng(png: Buffer): Pick<EyesSheet, 'width' | 'height' | 'pixels'>;
export declare function metaSource(sheet: EyesSheet): string;
