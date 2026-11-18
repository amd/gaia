// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Children, createContext, useContext, type ReactNode } from 'react';
import { Platform, Text as RNText, View, type AccessibilityRole, type TextStyle } from 'react-native';
import { glossParts, stripIsolates } from '@webui/i18n/registry';
import { useDirection } from '@/i18n/useT';
import { metrics } from '../tokens/generated';
import { fontFamily, type Weight } from '../theme/fonts';
import { COMPACT_MAX_FONT_SCALE, useLargeText } from '../theme/a11y';
import { useTheme } from '../theme/ThemeProvider';
import { toneColor, type Tone } from '../theme/tone';
import { ForcedDirection } from './ForcedDirection';
import { contentDir } from './direction';
import type { TypeRole } from '../tokens/type';

export interface TextProps {
    children?: ReactNode;
    role?: TypeRole;
    tone?: Tone;
    weight?: Weight;
    mono?: boolean;
    italic?: boolean;
    align?: 'start' | 'center' | 'end';
    /** Lines to show at normal text size; a person who chose a large size gets the whole text (up to 3 lines) unless `compact`. */
    numberOfLines?: number;
    /** A label inside a fixed-size control: stops scaling at 1.3x and keeps `numberOfLines`. Give the control the full label in `accessibilityLabel`. */
    compact?: boolean;
    selectable?: boolean;
    /** Where a one-line text is cut; `middle` keeps both ends of a path. */
    ellipsize?: 'tail' | 'middle';
    strike?: boolean;
    underline?: boolean;
    /** Inline code: mono on the code surface. */
    code?: boolean;
    /** Writing direction of this run, for content whose language differs from the UI's. `auto` follows the first strong letter of a string child. */
    dir?: 'ltr' | 'rtl' | 'auto';
    /** Keeps a long English gloss inline in brackets instead of a block under the text: for labels inside a control. */
    glossInline?: boolean;
    onPress?: () => void;
    accessibilityLabel?: string;
    accessibilityRole?: AccessibilityRole;
    testID?: string;
}

const InsideText = createContext(false);

const GLOSS_MIN_SIZE = 11;
const GLOSS_LINE_HEIGHT = 1.3;
const LONG_GLOSS_LENGTH = 40;

/** Whether a string child carries an English gloss. */
function hasGloss(children: ReactNode): boolean {
    let found = false;
    Children.forEach(children, (child) => {
        if (typeof child === 'string' && glossParts(child)) found = true;
    });
    return found;
}

/** A gloss that is a sentence (or several), or longer than `limit` characters, wraps badly inline, so it gets its own block. */
export function isLongGloss(english: string, limit = LONG_GLOSS_LENGTH): boolean {
    const sentence = /[.!?]$/.test(english) && english.split(/\s+/).length > 3;
    return english.length > limit || sentence || /[.!?]\s+\S/.test(english);
}

/** The English of a gloss part, without its isolates and brackets. */
function glossEnglish(part: string): string {
    return stripIsolates(part).trim().replace(/^\(/, '').replace(/\)$/, '');
}

/** Splits each string child at its English glosses and sets them small and muted, as the web's `.i18n-gloss` does; with `blocks`, long glosses are moved into it instead. */
function withGlosses(children: ReactNode, size: number, color: string, blocks: string[] | null, limit = LONG_GLOSS_LENGTH): ReactNode {
    const gloss = Math.max(size * 0.8, GLOSS_MIN_SIZE);
    const style = { fontSize: gloss, lineHeight: Math.round(gloss * GLOSS_LINE_HEIGHT), fontWeight: '400' as const, color, writingDirection: 'ltr' as const };
    let changed = false;
    const mapped = Children.map(children, (child, i) => {
        if (typeof child !== 'string') return child;
        const parts = glossParts(child);
        if (!parts) return child;
        changed = true;
        return parts.map((part, j) => {
            if (!part.gloss) {
                const next = parts[j + 1];
                return blocks && next?.gloss && isLongGloss(glossEnglish(next.text), limit) ? part.text.trimEnd() : part.text;
            }
            const english = glossEnglish(part.text);
            if (blocks && isLongGloss(english, limit)) {
                blocks.push(english);
                return null;
            }
            return (
                <RNText key={`${i}.${j}`} style={style}>
                    {part.text}
                </RNText>
            );
        });
    });
    return changed ? mapped : children;
}

export function Text({
    children,
    role,
    tone,
    weight,
    mono,
    italic,
    align,
    numberOfLines,
    compact,
    selectable,
    ellipsize,
    strike,
    underline,
    code,
    dir: dirProp,
    glossInline,
    onPress,
    accessibilityLabel,
    accessibilityRole,
    testID,
}: TextProps) {
    const { colors, type, space } = useTheme();
    const uiDir = useDirection();
    const rtl = uiDir === 'rtl';
    const dir = dirProp === 'auto' ? (typeof children === 'string' ? contentDir(children, uiDir) : uiDir) : dirProp;
    const nested = useContext(InsideText);
    const forced = useContext(ForcedDirection);
    const largeText = useLargeText();
    const lines = !compact && largeText && numberOfLines !== undefined && numberOfLines <= 3 ? undefined : numberOfLines;

    // A nested Text is a span: it inherits everything it isn't given.
    const baseSize = type[role ?? 'body'];
    const style: { -readonly [K in keyof TextStyle]: TextStyle[K] } = {};
    const display = role === 'display';
    if (!nested || role || weight || mono || code || tone) {
        const r: TypeRole = role ?? 'body';
        const w: Weight = weight ?? 'regular';
        style.fontFamily = fontFamily({ weight: w, mono: !!mono || !!code, display });
        if (!nested || role) {
            const size = type[r];
            const ratio = rtl
                ? r === 'prose' || r === 'body'
                    ? metrics.lineHeightArProse
                    : metrics.lineHeightAr
                : metrics.lineHeight;
            style.fontSize = size;
            style.lineHeight = Math.round(size * ratio);
        }
    }
    if (!nested || tone) style.color = toneColor(colors, tone ?? 'primary');
    if (italic) style.fontStyle = 'italic';
    if (code) {
        style.backgroundColor = colors.bgCode;
        style.color = colors.textCode;
    }
    if (strike || underline) style.textDecorationLine = strike && underline ? 'underline line-through' : strike ? 'line-through' : 'underline';
    if (dir) style.writingDirection = dir;
    // iOS lays a paragraph with a nested span out left to right unless told the UI direction.
    else if (Platform.OS !== 'web' && !nested && !dirProp && !forced && hasGloss(children)) style.writingDirection = uiDir;
    // textAlign left/right are physical on native too (not swapped under forceRTL), so the start side is picked by direction.
    // A pinned-direction block (HScroll) sets `forced` for text that carries no direction of its own.
    const startsRight = (dir ?? forced ?? (rtl ? 'rtl' : 'ltr')) === 'rtl';
    if (align === 'center') style.textAlign = 'center';
    else if (align === 'end') style.textAlign = startsRight ? 'left' : 'right';
    else if (align === 'start' || !nested) style.textAlign = startsRight ? 'right' : 'left';
    if (rtl) style.letterSpacing = 0;

    const blocks: string[] | null = !nested && !glossInline && !compact && numberOfLines === undefined ? [] : null;
    // Larger type wraps sooner, so headings move a shorter gloss to its own line.
    const glossLimit = Math.round((LONG_GLOSS_LENGTH * type.body) / baseSize);
    const body = withGlosses(children, baseSize, toneColor(colors, 'muted'), blocks, glossLimit);
    const glossSize = Math.max(baseSize * 0.8, GLOSS_MIN_SIZE);
    const blockStyle: TextStyle = {
        fontFamily: fontFamily({ weight: 'regular', mono: false, display: false }),
        fontSize: glossSize,
        lineHeight: Math.round(glossSize * GLOSS_LINE_HEIGHT),
        color: toneColor(colors, 'muted'),
        writingDirection: 'ltr',
        // The gloss sits on the same reading edge as the text it translates, though it reads left to right.
        textAlign: style.textAlign ?? 'left',
        marginTop: space.xs,
    };
    const main = (
        <InsideText.Provider value>
            <RNText
                style={style}
                numberOfLines={lines}
                maxFontSizeMultiplier={compact ? COMPACT_MAX_FONT_SCALE : undefined}
                ellipsizeMode={ellipsize ?? 'tail'}
                selectable={selectable}
                onPress={onPress}
                accessibilityLabel={accessibilityLabel}
                accessibilityRole={accessibilityRole}
                testID={testID}
            >
                {body}
            </RNText>
        </InsideText.Provider>
    );
    if (!blocks?.length) return main;
    return (
        // Shrinks inside a row like the Text it replaces.
        <View style={{ flexShrink: 1 }}>
            {main}
            {blocks.map((english, i) => (
                <RNText key={i} style={blockStyle} maxFontSizeMultiplier={compact ? COMPACT_MAX_FONT_SCALE : undefined} testID={testID ? `${testID}-gloss` : undefined}>
                    {english}
                </RNText>
            ))}
        </View>
    );
}
