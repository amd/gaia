// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import { ScrollView, View } from 'react-native';
import { useDirection } from '@/i18n/useT';
import { Icon } from '../controls/Icon';
import { ForcedDirection } from '../text/ForcedDirection';
import { ChevronRight } from '../icons';
import { useTheme } from '../theme/ThemeProvider';

export interface HScrollProps {
    children?: ReactNode;
    dir?: 'ltr' | 'rtl';
    testID?: string;
    /** Shows a chevron at the far edge while there is more to scroll to. */
    hint?: boolean;
}

/** Content wider than its box scrolls sideways inside it and never widens the page. */
export function HScroll({ children, dir, testID, hint = false }: HScrollProps) {
    const { colors, radius } = useTheme();
    const uiDir = useDirection();
    const [box, setBox] = useState({ view: 0, content: 0 });
    const [offset, setOffset] = useState(0);
    const more = hint && box.content > box.view + 1 && Math.abs(offset) < box.content - box.view - 1;
    const flip = (dir ?? uiDir) !== uiDir;
    return (
        <View style={dir ? { direction: dir } : undefined}>
            <ScrollView
                horizontal
                showsHorizontalScrollIndicator
                nestedScrollEnabled
                onLayout={(e) => setBox((b) => ({ ...b, view: e.nativeEvent.layout.width }))}
                onContentSizeChange={(w) => setBox((b) => ({ ...b, content: w }))}
                onScroll={hint ? (e) => setOffset(e.nativeEvent.contentOffset.x) : undefined}
                scrollEventThrottle={64}
                testID={testID}
            >
                <ForcedDirection.Provider value={dir ?? null}>{children}</ForcedDirection.Provider>
            </ScrollView>
            {more ? (
                <View
                    pointerEvents="none"
                    testID={testID ? `${testID}-more` : undefined}
                    style={{
                        position: 'absolute',
                        end: 0,
                        top: 0,
                        bottom: 0,
                        justifyContent: 'center',
                        paddingHorizontal: 2,
                        backgroundColor: colors.bgSecondary,
                        borderStartWidth: 1,
                        borderColor: colors.border,
                        borderTopStartRadius: radius.xs,
                        borderBottomStartRadius: radius.xs,
                        opacity: 0.92,
                    }}
                >
                    <View style={flip ? { transform: [{ scaleX: -1 }] } : undefined}>
                        <Icon icon={ChevronRight} size="sm" tone="muted" />
                    </View>
                </View>
            ) : null}
        </View>
    );
}
