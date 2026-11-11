// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** react/jsx-runtime, with bracketed labels split so CSS can quiet their English. */

import { jsx as reactJsx, jsxs as reactJsxs } from 'react/jsx-runtime';
import { withGloss } from './gloss';

export { Fragment } from 'react/jsx-runtime';
export type { JSX } from 'react/jsx-runtime';

export const jsx: typeof reactJsx = (type, props, key) => reactJsx(type, withGloss(type, props), key);
export const jsxs: typeof reactJsxs = (type, props, key) => reactJsxs(type, withGloss(type, props), key);
