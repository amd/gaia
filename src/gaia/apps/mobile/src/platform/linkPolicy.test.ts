// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { checkLink } from './linkPolicy';

describe('checkLink', () => {
    it.each(['https://amd-gaia.ai/docs', 'HTTP://example.com/a?b=c', 'mailto:me@example.com', '  https://example.com  '])('allows %s', (url) => {
        expect(checkLink(url).allowed).toBe(true);
    });

    it.each([
        ['file:///C:/Windows/System32/calc.exe', 'file'],
        ['smb://host/share/x.exe', 'smb'],
        ['javascript:alert(1)', 'javascript'],
        ['java\nscript:alert(1)', 'javascript'],
        ['data:text/html,<script>1</script>', 'data'],
        ['gaia://pair?code=1', 'gaia'],
        ['tel:+15551234', 'tel'],
        ['https:evil', 'https'],
        ['//host/share/x.exe', null],
        ['/Windows/System32/calc.exe', null],
        ['', null],
    ])('refuses %j', (url, scheme) => {
        expect(checkLink(url)).toEqual({ allowed: false, scheme });
    });
});
