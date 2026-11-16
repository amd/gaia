// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext } from 'react';

export const FileScopeContext = createContext<string | null>(null);

/** The chat whose folders a file link may read, or null where none is in scope (then a PC file offers no preview). */
export function useFileScope(): string | null {
    return useContext(FileScopeContext);
}
