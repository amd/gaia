// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Languages } from 'lucide-react';
import { LOCALES, localeOptionLabel } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';

/** The interface language, from the first screen on; the same setting as Settings → General. */
export function LanguagePicker() {
    const t = useT();
    const locale = useChatStore((s) => s.locale);
    const setLocale = useChatStore((s) => s.setLocale);
    return (
        <label className="setup-language">
            <Languages size={14} aria-hidden="true" />
            <span className="visually-hidden">{t('settings.language')}</span>
            <select value={locale} onChange={(e) => setLocale(e.target.value)}>
                {LOCALES.map(({ meta }) => (
                    <option key={meta.code} value={meta.code} lang={meta.code} dir={meta.dir}>
                        {localeOptionLabel(meta)}
                    </option>
                ))}
            </select>
        </label>
    );
}
