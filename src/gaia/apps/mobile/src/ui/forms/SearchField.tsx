// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { useT } from '@/i18n/useT';
import { Icon } from '../controls/Icon';
import { IconButton } from '../controls/IconButton';
import { TextField } from '../controls/TextField';
import { Search, X } from '../icons';

export interface SearchFieldProps {
    value: string;
    onChangeText: (text: string) => void;
    placeholder?: string;
    /** Wait this long after the last keystroke before `onChangeText`; clearing is immediate. */
    debounceMs?: number;
    onSubmitEditing?: () => void;
    autoFocus?: boolean;
    disabled?: boolean;
    accessibilityLabel?: string;
    testID?: string;
}

export function SearchField({ value, onChangeText, placeholder, debounceMs = 0, onSubmitEditing, autoFocus, disabled, accessibilityLabel, testID }: SearchFieldProps) {
    const t = useT();
    const [text, setText] = useState(value);
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const [seen, setSeen] = useState(value);
    if (value !== seen) {
        setSeen(value);
        setText(value);
    }
    useEffect(() => () => clearTimeout(timer.current), []);

    const change = (next: string) => {
        setText(next);
        clearTimeout(timer.current);
        if (debounceMs > 0) timer.current = setTimeout(() => onChangeText(next), debounceMs);
        else onChangeText(next);
    };

    return (
        <TextField
            value={text}
            onChangeText={change}
            placeholder={placeholder}
            accessibilityLabel={accessibilityLabel ?? placeholder}
            returnKeyType="search"
            onSubmitEditing={onSubmitEditing}
            autoFocus={autoFocus}
            disabled={disabled}
            testID={testID}
            leading={<Icon icon={Search} size="sm" tone="muted" />}
            trailing={
                text ? (
                    <IconButton
                        icon={X}
                        size="sm"
                        label={t('fileBrowser.clearSearch')}
                        onPress={() => {
                            clearTimeout(timer.current);
                            setText('');
                            onChangeText('');
                        }}
                    />
                ) : undefined
            }
        />
    );
}
