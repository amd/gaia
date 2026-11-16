// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { settingsHref } from './settingsHref';

describe('settingsHref', () => {
  it('carries the section, target and origin, and omits what is absent', () => {
    expect(settingsHref('privacy', 'cloud-key', 'setup')).toEqual({ pathname: '/settings/[section]', params: { section: 'privacy', target: 'cloud-key', from: 'setup' } });
    expect(settingsHref('model', 'models-on-pc', 'setup', 'fireworks').params).toMatchObject({ provider: 'fireworks' });
    expect(settingsHref('model')).toEqual({ pathname: '/settings/[section]', params: { section: 'model' } });
  });
});
