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

// A file the turn only read is not a result, even when it was saved moments
// before the turn: read by a tool, or shown by a command that writes nothing.
func TestAFileThatWasOnlyReadIsNotADeliverable(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	start := time.Now()
	note := filepath.Join(dir, "Documents", "note.txt")
	writeFile(t, note, "one two three four")
	report := filepath.Join(dir, "report.md")
	writeFile(t, report, "# Report")
	answer := "Documents/note.txt says: one two three four."

	read := ActivityItem{Kind: "tool", Tool: "file_read", Done: true, Call: callInfo{Category: catRead, Reads: []string{note}}}
	if got := findDeliverables([]ActivityItem{read}, answer, start); len(got) != 0 {
		t.Errorf("a file read by a tool is listed as created: %+v", got)
	}
	for _, cmd := range []string{"cat Documents/note.txt", "cd Documents && head -5 note.txt 2>/dev/null | wc -l", "grep -n two Documents/note.txt 2>&1"} {
		shown := ActivityItem{Kind: "tool", Tool: "bash_execute", Done: true, Call: callInfo{Category: catShell, Command: cmd}}
		if got := findDeliverables([]ActivityItem{shown}, answer, start); len(got) != 0 {
			t.Errorf("%q: a file a command only showed is listed as created: %+v", cmd, got)
		}
	}

	// Read, then changed: it is a result.
	for _, cmd := range []string{"cat Documents/note.txt > report.md", "python3 fix.py Documents/note.txt", "sed -i '' s/one/1/ Documents/note.txt", "cat x | tee Documents/note.txt"} {
		ran := ActivityItem{Kind: "tool", Tool: "bash_execute", Done: true, Call: callInfo{Category: catShell, Command: cmd}}
		got := findDeliverables([]ActivityItem{read, ran}, "", start)
		want := "note.txt"
		if strings.Contains(cmd, "> report.md") {
			want = "report.md"
		}
		found := false
		for _, d := range got {
			found = found || filepath.Base(d.Path) == want
		}
		if !found {
			t.Errorf("%q: %s is missing from %+v", cmd, want, got)
		}
	}
	edit := ActivityItem{Kind: "tool", Tool: "file_edit", Done: true, Call: callInfo{Category: catEdit, Writes: []string{note}}}
	if got := findDeliverables([]ActivityItem{read, edit}, answer, start); len(got) != 1 {
		t.Errorf("a file read and then edited is missing: %+v", got)
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

// Ctrl+F opens documents only. A script or an executable the agent wrote is
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
	if d, ok := m.newerDeliverable(); !ok || filepath.Base(d.Path) != "report.xlsx" {
		t.Errorf("newerDeliverable = %+v, %v; want the document, skipping the script", d, ok)
	}
}

// Ctrl+F refuses what the media opener refuses: an SSH session, where the
// viewer would launch on the far machine, and paths a Windows launcher would
// reinterpret (a share, a device path, an NTFS stream, a %VAR%).
func TestOpenFileRefusesWhatMediaRefuses(t *testing.T) {
	t.Setenv("SSH_CONNECTION", "")
	t.Setenv("SSH_TTY", "")
	for _, p := range []string{
		`\\server\share\report.xlsx`, `//server/share/report.pdf`, `\??\C:\x\notes.txt`,
		`C:\x\run.exe:hidden.txt`, `C:\%TEMP%\report.csv`,
	} {
		msg := openFile(p)().(fileOpenResultMsg)
		if msg.err == nil {
			t.Errorf("%s was handed to the system opener", p)
		}
	}

	m := newTestChat(t)
	m.streaming = false
	m.messages = append(m.messages, Message{Role: RoleAssistant, Deliverables: []deliverable{{Path: "C:/x/report.xlsx"}}})
	if !strings.Contains(joinHints(m.statusHints()), "Ctrl+F open file") {
		t.Fatalf("locally the hint bar should offer Ctrl+F: %q", joinHints(m.statusHints()))
	}

	t.Setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22")
	msg := openFile(filepath.Join(t.TempDir(), "report.xlsx"))().(fileOpenResultMsg)
	if msg.err == nil || !strings.Contains(msg.err.Error(), "SSH") {
		t.Errorf("over SSH Ctrl+F should refuse with a reason, got %v", msg.err)
	}
	for _, h := range m.statusHints() {
		if strings.Contains(h.text, "Ctrl+F open file") {
			t.Errorf("the hint bar offers Ctrl+F over SSH: %q", h.text)
		}
	}
}

func TestAReaderGivenAnOutputFileWrites(t *testing.T) {
	for _, cmd := range []string{
		"sort -o out.txt in.txt", "sort --output=out.txt in.txt", "sort --output out.txt in.txt",
		"sort -uo out.txt in.txt", "sort -oout.txt in.txt", "uniq in.txt out.txt", "cat a | uniq -c - out.txt",
	} {
		if onlyReadsFiles(cmd) {
			t.Errorf("%q writes a file but was taken as read-only", cmd)
		}
	}
	for _, cmd := range []string{"sort in.txt", "sort -u -k2 in.txt", "uniq -c in.txt", "sort -t, -n in.txt | uniq"} {
		if !onlyReadsFiles(cmd) {
			t.Errorf("%q only reads but was taken as a write", cmd)
		}
	}
}
