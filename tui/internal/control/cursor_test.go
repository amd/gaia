package control

import "testing"

func TestCursorCellsFindsReverseVideoCellAndItsLeftNeighbours(t *testing.T) {
	frame := "header\n\x1b[40m\x1b[37m┃ \x1b[0m\x1b[7m \x1b[0m  rest\n"
	got := CursorCells(frame)
	if len(got) != 1 {
		t.Fatalf("want 1 cursor cell, got %d: %+v", len(got), got)
	}
	c := got[0]
	if c.Row != 1 || c.Col != 2 || c.Left != "┃ " {
		t.Errorf("got %+v, want row 1 col 2 left %q", c, "┃ ")
	}
}

func TestCursorCellsIgnoresWideReverseRuns(t *testing.T) {
	// A highlighted label is reverse video too, but it is not a cursor.
	if got := CursorCells("\x1b[7m selected \x1b[0m"); len(got) != 0 {
		t.Errorf("want no cursor cells, got %+v", got)
	}
}
