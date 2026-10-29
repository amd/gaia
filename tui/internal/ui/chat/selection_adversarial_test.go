// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
)

// tickUntilIdle runs auto-scroll ticks until the ticker stands down, failing
// if it never does.
func tickUntilIdle(t *testing.T, m ChatModel, limit int) ChatModel {
	t.Helper()
	for i := 0; i < limit; i++ {
		next, cmd := m.Update(selectScrollTickMsg{})
		m = next.(ChatModel)
		if cmd == nil {
			return m
		}
	}
	t.Fatalf("auto-scroll was still ticking after %d ticks", limit)
	return m
}

// ── The transcript changing under a drag ─────────────────────────────────

// Answers keep streaming in while the user drags: the selection must stay on
// the rows it started on and copy the lines the user saw, not shifted ones.
func TestNewMessagesArrivingMidDragDoNotShiftTheSelection(t *testing.T) {
	m := idleChat(t, 80, 14, numberedStatus(30)...)
	m.viewport.GotoTop()
	m = m.afterScroll()
	read := captureClipboard(t)

	m, _ = mouse(t, m, tea.MouseActionPress, 2, rowOf(t, m, "line 01"))
	m, _ = mouse(t, m, tea.MouseActionMotion, 40, rowOf(t, m, "line 03"))
	for i := 0; i < 20; i++ {
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: fmt.Sprintf("late %02d", i)})
		m.updateViewport()
	}
	m, cmd := mouse(t, m, tea.MouseActionRelease, 40, rowOf(t, m, "line 03"))
	got := read(cmd)
	if !strings.Contains(got, "line 01") || !strings.Contains(got, "line 03") || strings.Contains(got, "late") {
		t.Errorf("selection drifted with the new messages: %q", got)
	}
}

// The transcript is cleared (or shrinks) while the button is still down:
// release must not index past the new content.
func TestTranscriptShrinkingMidDragDoesNotPanic(t *testing.T) {
	m := idleChat(t, 80, 14, numberedStatus(60)...)
	m.viewport.GotoBottom()
	m = m.afterScroll()
	captureClipboard(t)
	top := m.contentHeaderRows()
	m, _ = mouse(t, m, tea.MouseActionPress, 2, top+2)
	m, _ = mouse(t, m, tea.MouseActionMotion, 9, top+5)

	m.messages = m.messages[:1]
	m.updateViewport()

	m, cmd := mouse(t, m, tea.MouseActionRelease, 9, top+5)
	if cmd != nil {
		_ = cmd()
	}
	m = tickUntilIdle(t, m, 5)
	_ = m.View()
	_ = m.selectionText()
}

// Streaming tokens re-render the live answer between motion events.
func TestStreamingTokensDuringADragKeepItCopyable(t *testing.T) {
	m := idleChat(t, 80, 14, numberedStatus(20)...)
	m.viewport.GotoTop()
	m = m.afterScroll()
	read := captureClipboard(t)
	m, _ = mouse(t, m, tea.MouseActionPress, 0, rowOf(t, m, "line 00"))
	m.streaming = true
	for i := 0; i < 50; i++ {
		m.buffer += fmt.Sprintf("tok%d ", i)
		m.updateViewport()
		m, _ = mouse(t, m, tea.MouseActionMotion, 40, rowOf(t, m, "line 02"))
	}
	_, cmd := mouse(t, m, tea.MouseActionRelease, 40, rowOf(t, m, "line 02"))
	if got := read(cmd); !strings.Contains(got, "line 00") || !strings.Contains(got, "line 02") {
		t.Errorf("copied %q", got)
	}
}

// ── Pointer far outside the window ───────────────────────────────────────

func TestDragFarBeyondTheBoundsIsClampedAndStops(t *testing.T) {
	for name, pt := range map[string][2]int{
		"far below":      {5, 100000},
		"far above":      {5, -100000},
		"far right":      {100000, 6},
		"far left":       {-100000, 6},
		"corner overrun": {1 << 30, 1 << 30},
	} {
		t.Run(name, func(t *testing.T) {
			m := idleChat(t, 80, 14, numberedStatus(60)...)
			m.viewport.SetYOffset(20)
			m = m.afterScroll()
			read := captureClipboard(t)
			m, _ = mouse(t, m, tea.MouseActionPress, 2, m.contentHeaderRows()+3)
			m, _ = mouse(t, m, tea.MouseActionMotion, pt[0], pt[1])
			before := m.viewport.YOffset
			next, _ := m.Update(selectScrollTickMsg{})
			m = next.(ChatModel)
			if moved := m.viewport.YOffset - before; moved > selectScrollMaxLines || moved < -selectScrollMaxLines {
				t.Errorf("one tick scrolled %d lines", moved)
			}
			m = tickUntilIdle(t, m, 200)
			if m.sel.head.row < 0 || m.sel.head.row >= m.viewport.TotalLineCount() {
				t.Errorf("head row %d is outside the transcript", m.sel.head.row)
			}
			_, cmd := mouse(t, m, tea.MouseActionRelease, pt[0], pt[1])
			got := read(cmd)
			if !utf8.ValidString(got) || got == "" {
				t.Errorf("copied %q", got)
			}
		})
	}
}

// ── Degenerate viewports ─────────────────────────────────────────────────

func TestTinyAndZeroHeightViewportsNeverPanicOrSpin(t *testing.T) {
	for _, size := range [][2]int{{80, 0}, {80, 1}, {80, 3}, {1, 24}, {0, 0}, {5, 2}} {
		t.Run(fmt.Sprintf("%dx%d", size[0], size[1]), func(t *testing.T) {
			m := idleChat(t, size[0], size[1], numberedStatus(10)...)
			captureClipboard(t)
			for _, y := range []int{-1, 0, 1, 2, 50} {
				m, _ = mouse(t, m, tea.MouseActionPress, 0, y)
				m, _ = mouse(t, m, tea.MouseActionMotion, 3, y+1)
				m = tickUntilIdle(t, m, 100)
				var cmd tea.Cmd
				m, cmd = mouse(t, m, tea.MouseActionRelease, 3, y+1)
				if cmd != nil {
					_ = cmd()
				}
			}
			_ = m.View()
		})
	}
}

func TestEmptyTranscriptSelectionIsHarmless(t *testing.T) {
	m := idleChat(t, 80, 14)
	m.vpContent = ""
	captureClipboard(t)
	top := m.contentHeaderRows()
	m, _ = mouse(t, m, tea.MouseActionPress, 1, top)
	m, _ = mouse(t, m, tea.MouseActionMotion, 9, top+50)
	m = tickUntilIdle(t, m, 10)
	_, cmd := mouse(t, m, tea.MouseActionRelease, 9, top+50)
	if cmd != nil {
		_ = cmd()
	}
	if got := extractSelection(nil, nil, nil, selPos{row: 3}, selPos{row: -9}); got != "" {
		t.Errorf("selection of nothing = %q", got)
	}
}

func TestReversedAndOutOfRangePositionsExtractSafely(t *testing.T) {
	lines := []string{"alpha", "beta", "gamma"}
	cases := [][2]selPos{
		{{row: 2, col: 3}, {row: 0, col: 1}},
		{{row: -5, col: -5}, {row: 99, col: 99}},
		{{row: 99, col: 0}, {row: 100, col: 0}},
		{{row: 1, col: 1 << 30}, {row: 1, col: 1 << 30}},
	}
	for _, c := range cases {
		got := extractSelection(lines, nil, nil, c[0], c[1])
		if !utf8.ValidString(got) {
			t.Errorf("%v → invalid text %q", c, got)
		}
	}
	if got := extractSelection(lines, nil, nil, selPos{row: 2, col: 4}, selPos{row: 0, col: 0}); got != "alpha\nbeta\ngamma" {
		t.Errorf("a backwards drag copied %q", got)
	}
}

// A selection whose edge lands inside a double-width character must not split
// it into invalid bytes.
func TestSelectionEdgesInsideWideCharactersStayValid(t *testing.T) {
	lines := []string{"总结这份报告 👍🏽 done"}
	for lo := 0; lo < 20; lo++ {
		for hi := lo; hi < 22; hi++ {
			got := extractSelection(lines, nil, nil, selPos{col: lo}, selPos{col: hi})
			if !utf8.ValidString(got) {
				t.Fatalf("cols %d..%d gave invalid UTF-8 %q", lo, hi, got)
			}
		}
	}
}

// ── OSC 52 with a huge transcript ────────────────────────────────────────

func TestCopyAllOfAHugeTranscriptIsOneCompleteOSC52(t *testing.T) {
	var msgs []Message
	line := strings.Repeat("ünïcödé — ``` fence 👍🏽 ", 40) + "\n"
	for i := 0; i < 400; i++ {
		msgs = append(msgs,
			Message{Role: RoleUser, Content: fmt.Sprintf("question %d", i)},
			Message{Role: RoleAssistant, Content: strings.Repeat(line, 10)},
		)
	}
	m := idleChat(t, 100, 30, msgs...)
	read := captureClipboard(t)
	_, cmd := m.submit("/copy all")
	got := read(cmd)
	want := transcriptMarkdown(m.messages, m.agentName)
	if len(want) < 4<<20 {
		t.Fatalf("test transcript is only %d bytes", len(want))
	}
	if got != want {
		t.Fatalf("a %d-byte transcript came back as %d bytes", len(want), len(got))
	}
}

func TestCopyingOnlyWhitespaceSaysNothingToCopy(t *testing.T) {
	cmd := copyToClipboard(" \n\t ", "selection")
	res, ok := cmd().(clipboardResultMsg)
	if !ok || res.err == nil {
		t.Errorf("whitespace was 'copied': %+v", res)
	}
}
