// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// RTL auto-cleanup relies on globals being injected; with globals: false
// we register the cleanup hook explicitly.
afterEach(() => {
    cleanup();
});
