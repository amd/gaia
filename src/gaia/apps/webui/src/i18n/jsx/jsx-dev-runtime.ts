// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** react/jsx-dev-runtime, with the same label split as ./jsx-runtime. */

import { jsxDEV as reactJsxDEV } from 'react/jsx-dev-runtime';
import { withGloss } from './gloss';

export { Fragment } from 'react/jsx-dev-runtime';
export type { JSX } from 'react/jsx-dev-runtime';

export const jsxDEV: typeof reactJsxDEV = (type, props, key, isStatic, source, self) =>
    reactJsxDEV(type, withGloss(type, props), key, isStatic, source, self);
