// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// Opening the palette must upgrade capture to All-Motion — the transcript
// already holds the mouse for the wheel, but hover needs motion reporting the
// plain mode does not ask for.
func TestOpeningThePaletteUpgradesCaptureToAllMotion(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	if !m.palette.open {
		t.Fatal("test setup: palette should have opened")
	}
	if !m.mouseCaptured {
		t.Error("opening the palette did not capture the mouse")
	}
	if !m.mouseCaptureAllMotion {
		t.Error("the palette needs hover, which needs All-Motion tracking")
	}
}

// Closing the overlay must step capture back DOWN rather than release it: the
// transcript still wants the wheel, and All-Motion tracking is noticeably
// chattier over SSH than Cell-Motion.
func TestClosingThePaletteStepsCaptureBackDown(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	if !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should have upgraded capture to All-Motion")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if !m.mouseCaptured {
		t.Error("closing the palette killed the transcript's wheel scrolling")
	}
	if m.mouseCaptureAllMotion {
		t.Error("no overlay is open — capture should have stepped back down to Cell-Motion")
	}
}

// SELECT MODE is a standing USER choice, and an overlay opening and closing
// around it must not silently fight it: the overlay still needs its clicks
// while it is up, and the mouse must go straight back to the terminal after.
func TestSelectModeSurvivesAnOverlayOpeningAndClosing(t *testing.T) {
	m, _ := newTestModel(t)
	m, _ = press(t, m, tea.KeyCtrlT)
	if !m.mouseSelectMode || m.mouseCaptured {
		t.Fatal("test setup: Ctrl+T should have handed the mouse to the terminal")
	}

	m = typeInto(t, m, "/")
	if !m.palette.open || !m.mouseCaptured || !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should be open with All-Motion capture")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if !m.mouseSelectMode {
		t.Error("the palette closing turned off the user's own select mode")
	}
	if m.mouseCaptured {
		t.Error("the palette closing kept a capture the user had asked to give up")
	}
}

// A question opening/closing must scope capture exactly the same way the
// palette does.
func TestOpeningAndClosingAQuestionScopesTheMouseTheSameWay(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())
	if m.question == nil {
		t.Fatal("test setup: expected a live question")
	}

	// feed() bypasses Update (see its own doc comment), so capture is
	// reconciled lazily on the next real Update — exactly like the real
	// program, which never calls handleEvent directly.
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyDown})
	m = updated.(ChatModel)
	if !m.mouseCaptured || !m.mouseCaptureAllMotion {
		t.Error("an open question did not capture the mouse for hover/click")
	}

	// Answering (rather than Esc) is the deterministic way to close a
	// question in this test model — modelWith sets no cancelFn, so Esc alone
	// would just clear the composer (see handleKey's idle-Esc fallthrough),
	// not the question itself.
	updated, _ = m.Update(components.QuestionAnsweredMsg{RequestID: "q1", Value: "yes"})
	m = updated.(ChatModel)
	if m.question != nil {
		t.Fatal("test setup: answering should have cleared the question")
	}
	if m.mouseCaptureAllMotion {
		t.Error("the question closing left hover tracking on with nothing to hover")
	}
	if !m.mouseCaptured {
		t.Error("the question closing killed the transcript's wheel scrolling")
	}
}
