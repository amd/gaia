// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export declare const SCHEMAS_PATH: string;
export declare const OUTPUT_PATH: string;
export declare const REGENERATE_HINT: string;
export declare function generateApiTypes(schemas: Record<string, unknown>): Promise<string>;
