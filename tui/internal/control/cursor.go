package control

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// CursorCell is one screen cell drawn the way a block cursor is drawn: a single
// reverse-video cell.
type CursorCell struct {
	Row  int    `json:"row"`
	Col  int    `json:"col"`
	Text string `json:"text"`
	// Left is the two cells drawn just before it. A bar glyph there ("┃ ")
	// reads as a second cursor beside the real one.
	Left string `json:"left"`
}

// CursorCells lists every cell in frame drawn as a block cursor. Neither the
// plain screen nor the hidden terminal cursor shows these, so this is how a
// driver checks that a focused input draws exactly one cursor.
func CursorCells(frame string) []CursorCell {
	var cells []CursorCell
	for row, line := range strings.Split(strings.TrimRight(frame, "\n"), "\n") {
		for _, r := range splitRuns(line) {
			if !r.state.reverse || r.width != 1 {
				continue
			}
			cells = append(cells, CursorCell{
				Row:  row,
				Col:  r.col,
				Text: r.text,
				Left: ansi.Strip(ansi.Cut(line, max(r.col-2, 0), r.col)),
			})
		}
	}
	return cells
}
