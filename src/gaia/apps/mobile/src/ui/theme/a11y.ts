// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Platform, useWindowDimensions } from 'react-native';

export interface A11yVisibility {
    accessible?: boolean;
    accessibilityElementsHidden?: boolean;
    importantForAccessibility?: 'auto' | 'yes' | 'no' | 'no-hide-descendants';
}

/** The screen-reader visibility props for native; on web they would reach the DOM as unknown attributes, so `aria-hidden` stands in. */
export function a11yVisibility(props: A11yVisibility): A11yVisibility & { 'aria-hidden'?: boolean } {
    if (Platform.OS !== 'web') return props;
    const hidden = props.accessibilityElementsHidden || props.importantForAccessibility === 'no-hide-descendants';
    return { ...(props.accessible ? { accessible: true } : {}), ...(hidden ? { 'aria-hidden': true } : {}) };
}

/** From here the person has chosen a larger text size (Dynamic Type's Large and up), so single-line rows give way to wrapping. */
export const LARGE_TEXT_SCALE = 1.35;

/** Compact controls (chips, tabs, badges) stop growing here; their full label is in `accessibilityLabel`. */
export const COMPACT_MAX_FONT_SCALE = 1.3;

export function useLargeText(): boolean {
    return useWindowDimensions().fontScale >= LARGE_TEXT_SCALE;
}
