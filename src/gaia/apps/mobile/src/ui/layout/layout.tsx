// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View, type AccessibilityRole, type DimensionValue, type ViewStyle } from 'react-native';
import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react';
import { Ltr } from '../text/Ltr';
import { Text } from '../text/Text';
import { useLargeText } from '../theme/a11y';
import { useTheme } from '../theme/ThemeProvider';
import type { Space } from '../tokens/space';

type Align = 'start' | 'center' | 'end' | 'stretch';
type Justify = 'start' | 'center' | 'end' | 'between';

const ALIGN = { start: 'flex-start', center: 'center', end: 'flex-end', stretch: 'stretch' } as const;
const JUSTIFY = { start: 'flex-start', center: 'center', end: 'flex-end', between: 'space-between' } as const;

interface BoxProps {
    children?: ReactNode;
    gap?: Space;
    align?: Align;
    justify?: Justify;
    flex?: number;
    /** Where this box sits across its parent's axis; `end` is the reading end in either direction. */
    self?: 'start' | 'center' | 'end';
    /** A centered box shrinks to its content unless it is given a width, e.g. '100%' under a `maxWidth`. */
    width?: DimensionValue;
    maxWidth?: DimensionValue;
    minHeight?: number;
    /** Layout direction of this box, for content whose language differs from the UI's. */
    dir?: 'ltr' | 'rtl';
    testID?: string;
    accessibilityRole?: AccessibilityRole;
}

export interface StackProps extends BoxProps {
    padding?: Space;
    paddingX?: Space;
    paddingY?: Space;
    /** Reads the whole box as one item with this label. */
    accessibilityLabel?: string;
}

export interface RowProps extends BoxProps {
    wrap?: boolean;
    /** At large text sizes the children stack, full width, instead of sharing the line (a pair of buttons). */
    stack?: boolean;
}

type Mut<T> = { -readonly [K in keyof T]: T[K] };

function boxStyle(space: Record<Space, number>, p: BoxProps): Mut<ViewStyle> {
    const style: Mut<ViewStyle> = {};
    if (p.gap) style.gap = space[p.gap];
    if (p.align) style.alignItems = ALIGN[p.align];
    if (p.justify) style.justifyContent = JUSTIFY[p.justify];
    if (p.flex !== undefined) style.flex = p.flex;
    if (p.dir) style.direction = p.dir;
    if (p.self) style.alignSelf = ALIGN[p.self];
    if (p.width !== undefined) style.width = p.width;
    if (p.maxWidth !== undefined) style.maxWidth = p.maxWidth;
    if (p.minHeight !== undefined) style.minHeight = p.minHeight;
    return style;
}

export function Stack({ children, padding, paddingX, paddingY, testID, accessibilityRole, accessibilityLabel, ...box }: StackProps) {
    const { space } = useTheme();
    const style = boxStyle(space, box);
    if (padding) style.padding = space[padding];
    if (paddingX) style.paddingHorizontal = space[paddingX];
    if (paddingY) style.paddingVertical = space[paddingY];
    return (
        <View
            style={style}
            testID={testID}
            accessibilityRole={accessibilityRole}
            accessible={accessibilityLabel ? true : undefined}
            accessibilityLabel={accessibilityLabel}
        >
            {children}
        </View>
    );
}

/** A text run beside an icon or control must be allowed to wrap rather than push the row wider than the screen. */
function shrinkable(child: ReactNode): ReactNode {
    if (isValidElement(child) && (child.type === Text || child.type === Ltr)) {
        return <View style={{ flexShrink: 1, minWidth: 0 }}>{child}</View>;
    }
    return child;
}

/** A `flex` share only means something along a row; in a column it would collapse the child. */
function unflexed(child: ReactNode): ReactNode {
    if (isValidElement(child) && child.type === Stack) return cloneElement(child as ReactElement<StackProps>, { flex: undefined });
    return child;
}

export function Row({ children, wrap, stack, align = 'center', testID, accessibilityRole, ...box }: RowProps) {
    const { space } = useTheme();
    const largeText = useLargeText();
    const style = boxStyle(space, { ...box, align });
    const stacked = !!stack && largeText;
    style.flexDirection = stacked ? 'column' : 'row';
    if (stacked) style.alignItems = 'stretch';
    // At large text a label beside its value stacks instead of squeezing or clipping.
    if (!stacked && (wrap || (largeText && box.justify === 'between' && wrap === undefined))) {
        style.flexWrap = 'wrap';
        // A wrapping row nested in another row must wrap inside it, not run past it at its one-line width.
        if (box.maxWidth === undefined) style.maxWidth = '100%';
    }
    return (
        <View style={style} testID={testID} accessibilityRole={accessibilityRole}>
            {stacked ? Children.map(children, unflexed) : largeText ? Children.map(children, shrinkable) : children}
        </View>
    );
}

export function Spacer() {
    return <View style={{ flex: 1 }} />;
}
