// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Ref } from 'react';
import { FlatList, Platform, SectionList as RNSectionList, type FlatListProps, type SectionListProps } from 'react-native';

const keyboard = {
    keyboardDismissMode: Platform.OS === 'ios' ? 'interactive' : 'on-drag',
    keyboardShouldPersistTaps: 'handled',
} as const;

export type ListProps<T> = FlatListProps<T> & { listRef?: Ref<FlatList<T>> };

export function List<T>({ listRef, ...props }: ListProps<T>) {
    return <FlatList ref={listRef} {...keyboard} {...props} />;
}

export type SectionListComponentProps<T, S> = SectionListProps<T, S> & { listRef?: Ref<RNSectionList<T, S>> };

export function SectionList<T, S = Record<string, unknown>>({ listRef, ...props }: SectionListComponentProps<T, S>) {
    return <RNSectionList ref={listRef} {...keyboard} {...props} />;
}
