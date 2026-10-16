// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"archive/zip"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
)

// writeWorkbook writes a minimal real .xlsx: three sheets, two with a declared
// extent, the way openpyxl and Excel both write them.
func writeWorkbook(t *testing.T, path string, when time.Time) {
	t.Helper()
	files := map[string]string{
		"xl/workbook.xml": `<?xml version="1.0"?><workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>` +
			`<sheet name="Register" sheetId="1" r:id="rId1"/><sheet name="Clauses" sheetId="2" r:id="rId2"/><sheet name="Summary" sheetId="3" r:id="rId3"/></sheets></workbook>`,
		"xl/_rels/workbook.xml.rels": `<?xml version="1.0"?><Relationships>` +
			`<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/>` +
			`<Relationship Id="rId3" Target="worksheets/sheet3.xml"/></Relationships>`,
		"xl/worksheets/sheet1.xml": `<?xml version="1.0"?><worksheet><dimension ref="A1:F9"/><sheetData/></worksheet>`,
		"xl/worksheets/sheet2.xml": `<?xml version="1.0"?><worksheet><dimension ref="A1:C65"/><sheetData/></worksheet>`,
		"xl/worksheets/sheet3.xml": `<?xml version="1.0"?><worksheet><sheetData/></worksheet>`,
	}
	writeZip(t, path, files)
	touch(t, path, when)
}

func writeZip(t *testing.T, path string, files map[string]string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	z := zip.NewWriter(f)
	for name, body := range files {
		w, err := z.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestPeekReadsInsideTheCommonDeliverables(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()

	xlsx := filepath.Join(dir, "r.xlsx")
	writeWorkbook(t, xlsx, now)
	if got := peekFile(xlsx); got != "3 sheets: Register (9 rows), Clauses (65 rows), Summary" {
		t.Errorf("workbook peek = %q", got)
	}

	pptx := filepath.Join(dir, "d.pptx")
	slides := map[string]string{"ppt/presentation.xml": "<p/>"}
	for i := 1; i <= 12; i++ {
		slides[fmt.Sprintf("ppt/slides/slide%d.xml", i)] = "<s/>"
		slides[fmt.Sprintf("ppt/slides/_rels/slide%d.xml.rels", i)] = "<r/>"
	}
	writeZip(t, pptx, slides)
	if got := peekFile(pptx); got != "12 slides" {
		t.Errorf("deck peek = %q", got)
	}

	docx := filepath.Join(dir, "m.docx")
	writeZip(t, docx, map[string]string{"word/document.xml": `<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Three carry</w:t></w:r><w:r><w:t xml:space="preserve"> uncapped liability.</w:t></w:r></w:p></w:body></w:document>`})
	if got := peekFile(docx); got != "4 words" {
		t.Errorf("document peek = %q", got)
	}

	csv := filepath.Join(dir, "t.csv")
	writeFile(t, csv, "a,b\n1,2\n3,4\n")
	if got := peekFile(csv); got != "2 rows" {
		t.Errorf("csv peek = %q", got)
	}
}

// A file that is not what its name says — a truncated zip, a text file called
// .xlsx, an empty docx — gets no peek, never a guess and never a crash.
func TestABrokenDeliverableGetsNoPeek(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{
		"fake.xlsx":  "not a zip at all",
		"empty.docx": "",
		"cut.pptx":   "PK\x03\x04truncated",
	} {
		p := filepath.Join(dir, name)
		writeFile(t, p, body)
		if got := peekFile(p); got != "" {
			t.Errorf("%s peeked as %q", name, got)
		}
	}
}

// Only files changed during the turn are results. An input the answer merely
// names, a path that does not exist, and a folder are not.
func TestOnlyFilesChangedDuringTheTurnAreDeliverables(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	start := time.Now()

	old := filepath.Join(dir, "input.xlsx")
	writeWorkbook(t, old, start.Add(-time.Hour))
	made := filepath.Join(dir, "out.xlsx")
	writeWorkbook(t, made, start.Add(time.Second))
	if err := os.MkdirAll(filepath.Join(dir, "folder.xlsx"), 0o755); err != nil {
		t.Fatal(err)
	}

	answer := "Read input.xlsx and wrote out.xlsx; also see missing.xlsx and folder.xlsx."
	got := findDeliverables(nil, answer, start)
	if len(got) != 1 || filepath.Base(got[0].Path) != "out.xlsx" {
		t.Fatalf("deliverables = %+v, want only out.xlsx", got)
	}
}

// The block names the file as an OSC 8 link to it, and the link is zero-width:
// the row still fits the terminal it is drawn on.
func TestTheDeliverableIsALinkThatFits(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	m, _ := frameChat(t, 60, 20)
	d := deliverable{Path: filepath.Join(dir, "contract_review.xlsx"), Size: 18 << 10, Peek: strings.Repeat("sheet ", 30)}
	rows := m.renderDeliverables([]deliverable{d})
	if !strings.Contains(rows[1], osc8Prefix+"file://") {
		t.Errorf("the file name is not a link: %q", rows[1])
	}
	for _, r := range rows {
		if w := ansi.StringWidth(r); w > 60 {
			t.Errorf("a deliverable row is %d columns on a 60-column terminal: %q", w, ansi.Strip(r))
		}
	}
}

// Ctrl+G opens documents only. A script or an executable the agent wrote is
// never launched by a keystroke.
func TestOnlyDocumentsOpenFromTheKeyboard(t *testing.T) {
	for _, name := range []string{"run.bat", "setup.exe", "x.ps1", "page.html", "evil.lnk", "macro.xlsm", "old.doc", "old.xls"} {
		msg := openFile(filepath.Join(t.TempDir(), name))().(fileOpenResultMsg)
		if msg.err == nil {
			t.Errorf("%s was handed to the system opener", name)
		}
	}
	m := ChatModel{messages: []Message{{Role: RoleAssistant, Deliverables: []deliverable{
		{Path: "C:/x/run.bat"}, {Path: "C:/x/report.xlsx"},
	}}}}
	if d, ok := m.lastDeliverable(); !ok || filepath.Base(d.Path) != "report.xlsx" {
		t.Errorf("lastDeliverable = %+v, %v; want the document, skipping the script", d, ok)
	}
}
