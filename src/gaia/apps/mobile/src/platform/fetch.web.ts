// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { FetchLike } from './fetch.types';

export { isPcUrl } from './pcUrl';

export const platformFetch: FetchLike = (url, init) => globalThis.fetch(url, init);
