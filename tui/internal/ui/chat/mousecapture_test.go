// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// Opening the palette must capture the mouse — otherwise a click on a row
// does nothing, and the whole feature is dead.
func TestOpeningThePaletteCapturesTheMouse(t *testing.T) {
	m, _ := newTestModel(t)
	if m.mouseCaptured {
		t.Fatal("test setup: mouse should start released")
	}

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

// The mouse must be released the moment the overlay closes — this is the
// whole point of scoping capture to the overlay rather than turning it back
// on globally (see ui/app.go's teaOptions doc comment).
func TestClosingThePaletteReleasesTheMouse(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	if !m.mouseCaptured {
		t.Fatal("test setup: palette should have captured the mouse")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if m.mouseCaptured {
		t.Error("closing the palette did not give the mouse back to the terminal")
	}
}

// The core constraint the task calls out explicitly: Ctrl+T's wheel mode is a
// standing USER choice, and an overlay opening and closing around it must not
// silently fight it — neither turning it off when the overlay closes, nor
// leaving it stuck in the overlay's own (All-Motion) tracking mode after.
func TestUserWheelModeSurvivesAnOverlayOpeningAndClosing(t *testing.T) {
	m, _ := newTestModel(t)
	m, _ = press(t, m, tea.KeyCtrlT)
	if !m.mouseWheelOn || !m.mouseCaptured || m.mouseCaptureAllMotion {
		t.Fatal("test setup: Ctrl+T should have turned on plain (Cell-Motion) wheel mode")
	}

	m = typeInto(t, m, "/")
	if !m.palette.open || !m.mouseCaptured || !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should be open, capture upgraded to All-Motion")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if !m.mouseWheelOn {
		t.Error("the palette closing turned off the user's own wheel mode")
	}
	if !m.mouseCaptured {
		t.Error("the palette closing released the mouse the user still wants for wheel scrolling")
	}
	if m.mouseCaptureAllMotion {
		t.Error("wheel mode alone needs no hover tracking — capture should have stepped back down to Cell-Motion")
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
	if m.mouseCaptured {
		t.Error("the question closing did not give the mouse back")
	}
}
