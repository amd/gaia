// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// Opening the palette must capture the mouse with All-Motion: hover needs motion
// reporting, and the terminal holds the mouse until an overlay asks for it.
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

// Closing the overlay hands the mouse straight back to the terminal: the
// palette needed it, the user did not ask for it, and drag-select must work
// again the moment the palette is gone.
func TestClosingThePaletteReleasesTheMouse(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	if !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should have captured with All-Motion")
	}
	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if m.mouseCaptured {
		t.Error("closing the palette kept the mouse, so drag-select stays broken")
	}
}

// With APP MOUSE on, closing an overlay steps capture back DOWN rather than
// releasing it: the user still wants clicks, and All-Motion tracking is
// noticeably chattier over SSH than Cell-Motion.
func TestClosingThePaletteStepsCaptureBackDown(t *testing.T) {
	m, _ := newTestModel(t)
	m, _ = press(t, m, tea.KeyCtrlT)
	m = typeInto(t, m, "/")
	if !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should have upgraded capture to All-Motion")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if !m.mouseCaptured {
		t.Error("closing the palette dropped the mouse the user gave the app")
	}
	if m.mouseCaptureAllMotion {
		t.Error("no overlay is open — capture should have stepped back down to Cell-Motion")
	}
}

// The default is a standing USER choice too, and an overlay opening and closing
// around it must not silently fight it: the overlay still needs its clicks
// while it is up, and the mouse must go straight back to the terminal after.
func TestTheDefaultSurvivesAnOverlayOpeningAndClosing(t *testing.T) {
	m, _ := newTestModel(t)
	if m.appMouse || m.mouseCaptured {
		t.Fatal("test setup: the terminal should own the mouse by default")
	}

	m = typeInto(t, m, "/")
	if !m.palette.open || !m.mouseCaptured || !m.mouseCaptureAllMotion {
		t.Fatal("test setup: palette should be open with All-Motion capture")
	}

	m, _ = press(t, m, tea.KeyEsc)
	if m.palette.open {
		t.Fatal("test setup: Esc should have closed the palette")
	}
	if m.appMouse {
		t.Error("the palette closing gave the app the mouse the user never asked for")
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
	if m.mouseCaptured {
		t.Error("the question closing kept the mouse, so drag-select stays broken")
	}
}
