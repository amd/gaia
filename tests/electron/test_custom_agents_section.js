// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Static assertions for CustomAgentsSection.tsx.
 *
 * Ensures export/import error paths log to console and that the status
 * banner scrolls into view when an error occurs.
 */

const path = require('path');
const fs = require('fs');

const CHAT_APP_PATH = path.join(__dirname, '../../src/gaia/apps/webui');

describe('CustomAgentsSection error handling', () => {
  let componentContent;

  beforeAll(() => {
    const componentPath = path.join(
      CHAT_APP_PATH,
      'src/components/CustomAgentsSection.tsx'
    );
    componentContent = fs.readFileSync(componentPath, 'utf8');
  });

  it('should log export failures to console', () => {
    expect(componentContent).toContain("console.error('Agent export failed:', err)");
  });

  it('should log import failures to console', () => {
    expect(componentContent).toContain("console.error('Agent import failed:', err)");
  });

  it('should declare an errorBannerRef for the status banner', () => {
    expect(componentContent).toContain('errorBannerRef');
  });

  it('should scroll the error banner into view on error', () => {
    expect(componentContent).toContain('scrollIntoView');
  });

  it('should trigger scroll only when status.kind === error', () => {
    expect(componentContent).toContain("status.kind === 'error'");
  });
});
