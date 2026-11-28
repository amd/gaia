// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue({
        current_path: '/home/user/documents/garden-club/planning/spring-2026/seed-orders',
        parent_path: '/home/user/documents/garden-club/planning/spring-2026',
        entries: [],
        quick_links: [],
    });
    useChatStore.setState({ currentSessionId: 'session-1', sessions: [], showFileBrowser: true });
});

afterEach(() => {
    delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth;
});

it('keeps Go up outside the scrolling path strip and scrolls the strip to the open folder', async () => {
    // jsdom has no layout; pretend the path is wider than its strip.
    Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => 900 });
    render(<FileBrowser onStartChat={vi.fn()} />);

    const goUp = await screen.findByTitle('Go up');
    const strip = document.querySelector('.fb-breadcrumb') as HTMLElement;
    expect(strip.contains(goUp)).toBe(false);
    expect(goUp.closest('.fb-pathbar')).not.toBeNull();
    expect(strip.scrollLeft).toBe(900);
});
