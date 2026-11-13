// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ModelRow } from '../ModelRow';
import { useChatStore } from '../../../stores/chatStore';
import type { ProviderModel } from '../../../types';

const FLASH: ProviderModel = {
    id: 'fireworks.deepseek-v4p1-flash', context_length: null, downloaded: true, labels: [],
    rank: 1, note: 'best overall, fastest', evidence: '14/14 tasks · quality 4.92/5 · $0.10 per 14-task run',
};

function renderRow(model: ProviderModel = FLASH) {
    const noop = () => {};
    return render(
        <ModelRow
            model={model} label="deepseek-v4p1-flash" current={false} remote busy={null} confirmingRemove={false}
            onUse={noop} onDownload={noop} onAskRemove={noop} onKeep={noop} onRemove={noop}
        />,
    );
}

/** The text a reader sees, without the bidi isolate marks around filled-in values. */
function seen(el: Element): string {
    return (el.textContent ?? '').replace(/[⁦-⁩]/g, '');
}

describe('ModelRow recommendation', () => {
    afterEach(() => useChatStore.setState({ locale: 'en' }));

    it('shows the rank note and measurement in English', () => {
        renderRow();
        expect(seen(document.querySelector('.model-badge.is-rank')!)).toBe('#1 best overall, fastest');
        expect(screen.getByText(/^Measured:/).textContent).toContain('14/14 tasks · quality 4.92/5 · $0.10 per 14-task run');
    });

    it('translates the rank note and measurement in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        renderRow();
        expect(seen(document.querySelector('.model-badge.is-rank')!)).toContain('#1 الأفضل إجمالًا والأسرع');
        const measured = seen(document.querySelectorAll('.model-row-meta')[0]);
        expect(measured).toContain('القياس: 14/14 مهمة · الجودة 4.92/5 · $0.10 لكل تشغيل من 14 مهمة');
    });

    it('shows a note and measurement it has no words for as sent', () => {
        useChatStore.setState({ locale: 'ar' });
        renderRow({ ...FLASH, note: 'new pick', evidence: 'run on 3 suites' });
        expect(seen(document.querySelector('.model-badge.is-rank')!)).toBe('#1 new pick');
        expect(seen(document.querySelectorAll('.model-row-meta')[0])).toContain('القياس: run on 3 suites');
    });
});
