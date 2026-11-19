// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

// The same cases as the Agent UI's speechText.test.ts, so both read a reply
// the same way.

func TestSpeakableTextSkipsWhatAVoiceCannotSay(t *testing.T) {
	md := strings.Join([]string{
		"## Results",
		"",
		"See **the report** at [our docs](https://amd-gaia.ai/docs) or https://example.com/x.",
		"",
		"```python",
		`print("hello")`,
		"```",
		"",
		"- first `item`",
		"1. second item",
		"> quoted",
		"![chart](chart.png)",
	}, "\n")
	want := "Results\nSee the report at our docs or\nfirst item\nsecond item\nquoted"
	if got := SpeakableText(md); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestSpeakableTextReadsATableAsItsCells(t *testing.T) {
	got := SpeakableText("| Model | Size |\n|---|---|\n| Gemma | 6 GB |")
	if want := "Model, Size\nGemma, 6 GB"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestSpeakableTextDropsEmphasisButKeepsSnakeCase(t *testing.T) {
	got := SpeakableText("Set _really_ `max_ctx_size` to __64K__.\n\n---\n\n* * *\nDone ~~now~~.")
	if want := "Set really max_ctx_size to 64K.\nDone now."; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestOnlyCodeIsEmpty(t *testing.T) {
	if got := SpeakableText("```\nls -la\n```"); got != "" {
		t.Fatalf("got %q", got)
	}
	if got := Pieces("```\nls -la\n```", MaxPieceChars); len(got) != 0 {
		t.Fatalf("got %q", got)
	}
}

func TestUnterminatedFenceIsSkippedToTheEnd(t *testing.T) {
	if got := SpeakableText("Run this:\n```\nrm -rf build"); got != "Run this:" {
		t.Fatalf("got %q", got)
	}
}

func longReply() string {
	var s []string
	for i := 0; i < 60; i++ {
		s = append(s, fmt.Sprintf("This is sentence number %d of a long reply.", i))
	}
	return strings.Join(s, " ")
}

func TestPiecesStartShortThenLongerKeepingEveryWord(t *testing.T) {
	long := longReply()
	pieces := Pieces(long, MaxPieceChars)
	if runeLen(pieces[0]) > FirstPieceChars {
		t.Errorf("first piece is %d runes, want <= %d", runeLen(pieces[0]), FirstPieceChars)
	}
	for _, p := range pieces[1:] {
		if runeLen(p) > PieceChars {
			t.Errorf("piece is %d runes, want <= %d", runeLen(p), PieceChars)
		}
	}
	if len(pieces) <= 2 {
		t.Errorf("got %d pieces, want more than 2", len(pieces))
	}
	if got := strings.Join(pieces, " "); got != long {
		t.Errorf("pieces lost or reordered words:\n%s", got)
	}
}

func TestPiecesCutASentenceLongerThanTheLimitAtASpace(t *testing.T) {
	run := strings.TrimSpace(strings.Repeat("word ", 300))
	pieces := Pieces(run, 100)
	for _, p := range pieces {
		if runeLen(p) > 100 {
			t.Errorf("piece is %d runes, want <= 100", runeLen(p))
		}
	}
	if got := strings.Join(pieces, " "); got != run {
		t.Error("pieces lost or reordered words")
	}
}

func TestPiecesSplitArabicAtItsQuestionMark(t *testing.T) {
	got := Pieces("ما هذا؟ هذا اختبار.", 12)
	if want := []string{"ما هذا؟", "هذا اختبار."}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}
