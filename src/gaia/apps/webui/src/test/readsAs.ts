// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { stripIsolates } from '../i18n/registry';

/** Text matcher for a label as read: ignores bidi isolates and the split around its English. */
export const readsAs = (text: string) => (content: string, element?: Element | null) =>
    stripIsolates(element?.tagName === 'I18N-LABEL' ? (element.textContent ?? '') : content) === text;
