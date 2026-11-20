// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { settingsHref } from './settingsHref';

describe('settingsHref', () => {
  it('carries the section, target and origin, and omits what is absent', () => {
    expect(settingsHref('about', 'pane', 'general')).toEqual({ pathname: '/settings/[section]', params: { section: 'about', target: 'pane', from: 'general' } });
    expect(settingsHref('notifications')).toEqual({ pathname: '/settings/[section]', params: { section: 'notifications' } });
  });
});
