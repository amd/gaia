// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext } from 'react';

/** Set by a block that pins its own direction (a diff, code): text inside aligns physically, as native does not swap left and right there. */
export const ForcedDirection = createContext<'ltr' | 'rtl' | null>(null);
