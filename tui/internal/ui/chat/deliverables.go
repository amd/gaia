// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"archive/zip"
	"bufio"
	"encoding/xml"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// For knowledge work the file IS the result. A turn that wrote a workbook, a
// deck or a report ends with it named: what it is, how big, a one-line peek
// inside it, and a link that opens it. Only files changed during the turn are
// listed, so an input the agent merely mentioned never shows up as "created".

// deliverable is one file a turn produced.
type deliverable struct {
	Path string // absolute
	Size int64
	Peek string
}

// deliverableMax caps how many files the block lists; the rest are counted.
const deliverableMax = 5

// deliverableSlack forgives a filesystem clock that rounds mtimes down.
const deliverableSlack = 2 * time.Second

// deliverableExts are the file types worth calling a result.
var deliverableExts = map[string]bool{
	"xlsx": true, "xlsm": true, "xls": true, "csv": true, "tsv": true,
	"docx": true, "doc": true, "pptx": true, "ppt": true, "pdf": true,
	"md": true, "txt": true, "json": true, "html": true, "htm": true,
	"png": true, "jpg": true, "jpeg": true, "svg": true, "zip": true,
}

// openableExts are the deliverables Ctrl+G may hand to the system opener:
// documents that cannot carry code. An executable, a script, a page or a
// macro-enabled or legacy Office file the agent wrote is never launched by a
// keystroke.
var openableExts = map[string]bool{
	"xlsx": true, "csv": true, "tsv": true, "docx": true, "pptx": true, "pdf": true,
	"md": true, "txt": true, "json": true, "png": true, "jpg": true, "jpeg": true,
}

var deliverablePathRe = regexp.MustCompile(
	`(?i)(?:[a-z]:[\\/]|[\\/]|\.{1,2}[\\/]|~[\\/])?[^\s"'<>|*?,;()\[\]{}` + "`" + `]*\.(?:` +
		`xlsx|xlsm|xls|csv|tsv|docx|doc|pptx|ppt|pdf|md|txt|json|html|htm|png|jpe?g|svg|zip)\b`)

// findDeliverables lists the files this turn created or changed: the ones its
// calls wrote, and the ones its commands or its answer name — kept only if they
// exist and changed since the turn began.
func findDeliverables(work []ActivityItem, answer string, since time.Time) []deliverable {
	// What a call was asked to write is a deliverable wherever it is. A path
	// that only appears in text — a command, the answer — is model output a
	// page or an email can steer, so it is only looked at under the folder
	// GAIA started in or the user's home.
	type candidate struct {
		path    string
		written bool
	}
	var candidates []candidate
	for _, it := range work {
		if !isStep(it) || failedItem(it) {
			continue
		}
		for _, w := range it.Call.Writes {
			candidates = append(candidates, candidate{w, true})
		}
		if c := it.Call.Command; c != "" {
			for _, p := range deliverablePathRe.FindAllString(c, -1) {
				candidates = append(candidates, candidate{p, false})
			}
		}
	}
	for _, p := range deliverablePathRe.FindAllString(answer, -1) {
		candidates = append(candidates, candidate{p, false})
	}
	if len(candidates) > deliverableScan {
		candidates = candidates[:deliverableScan]
	}

	seen := map[string]bool{}
	var out []deliverable
	for _, c := range candidates {
		abs := resolveCandidate(c.path)
		if abs == "" || seen[strings.ToLower(abs)] || remotePath(abs) {
			continue
		}
		if !c.written && !underLocalRoot(abs) {
			continue
		}
		seen[strings.ToLower(abs)] = true
		info, err := os.Stat(abs)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		if !since.IsZero() && info.ModTime().Before(since.Add(-deliverableSlack)) {
			continue
		}
		if !deliverableExts[extOf(abs)] {
			continue
		}
		out = append(out, deliverable{Path: abs, Size: info.Size(), Peek: peekFile(abs)})
	}
	return out
}

// deliverableScan bounds how many candidate paths one turn may stat.
const deliverableScan = 40

// remotePath reports a UNC, device or network path. Statting one can block on
// the network, and a path the model named should never make the TUI reach out.
func remotePath(p string) bool {
	return strings.HasPrefix(p, `\\`) || strings.HasPrefix(p, "//")
}

// underLocalRoot reports whether p sits under the folder GAIA started in or the
// user's home folder.
func underLocalRoot(p string) bool {
	for _, root := range []string{pathBase, homeDir} {
		if root == "" {
			continue
		}
		if rel, err := filepath.Rel(root, p); err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel) {
			return true
		}
	}
	return false
}

func resolveCandidate(p string) string {
	p = strings.Trim(strings.TrimSpace(p), "`'\".,:;")
	if p == "" {
		return ""
	}
	if strings.HasPrefix(p, "~") && homeDir != "" {
		p = filepath.Join(homeDir, p[1:])
	}
	if !filepath.IsAbs(p) && !(runtime.GOOS == "windows" && len(p) > 2 && p[1] == ':') {
		if pathBase == "" {
			return ""
		}
		p = filepath.Join(pathBase, p)
	}
	return filepath.Clean(p)
}

func extOf(p string) string {
	return strings.ToLower(strings.TrimPrefix(filepath.Ext(p), "."))
}

var (
	deliverableLabelStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	deliverableNameStyle  = lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
)

// renderDeliverables is the "Created" block under an answer.
func (m ChatModel) renderDeliverables(ds []deliverable) []string {
	if len(ds) == 0 {
		return nil
	}
	width := max(m.width-6, 16)
	lines := []string{"  " + deliverableLabelStyle.Render("Created")}
	nameW := 0
	for _, d := range ds[:min(len(ds), deliverableMax)] {
		nameW = max(nameW, displayWidth(clean(filepath.Base(d.Path))))
	}
	nameW = min(nameW, width/2)
	for i, d := range ds {
		if i == deliverableMax {
			lines = append(lines, "    "+foldLineStyle.Render(fmt.Sprintf("… %d more %s", len(ds)-i, pluralize("files", len(ds)-i))))
			break
		}
		name := clean(filepath.Base(d.Path))
		meta := []string{humanSize(d.Size)}
		if d.Peek != "" {
			meta = append(meta, d.Peek)
		}
		if dir := filepath.Dir(d.Path); !strings.EqualFold(dir, pathBase) {
			meta = append(meta, shortenPath(clean(dir)))
		}
		name = truncateRunes(name, nameW)
		pad := strings.Repeat(" ", max(nameW-displayWidth(name), 0))
		metaText := truncateRunes(strings.Join(meta, " · "), max(width-nameW-2, 1))
		lines = append(lines, "    "+fileLink(d.Path, deliverableNameStyle.Render(name))+pad+"  "+activityStyle.Render(metaText))
	}
	return lines
}

// fileLink wraps styled text in an OSC 8 link to path, so a terminal that
// supports it opens the file on click. Zero-width: column math is unchanged.
func fileLink(path, text string) string {
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(path)}
	if !strings.HasPrefix(u.Path, "/") {
		u.Path = "/" + u.Path
	}
	return osc8Prefix + u.String() + osc8Sep + text + osc8Close
}

func humanSize(n int64) string {
	switch {
	case n < 1024:
		return fmt.Sprintf("%d B", n)
	case n < 1024*1024:
		return fmt.Sprintf("%d KB", (n+512)/1024)
	}
	return fmt.Sprintf("%.1f MB", float64(n)/(1024*1024))
}

// --- peeking inside ----------------------------------------------------------

// peekMaxBytes bounds how much of any one file a peek reads.
const peekMaxBytes = 8 << 20

// peekFile is a one-line look inside a deliverable, or "" when there is nothing
// cheap and certain to say.
func peekFile(path string) string {
	switch extOf(path) {
	case "xlsx", "xlsm":
		return peekWorkbook(path)
	case "pptx":
		return peekDeck(path)
	case "docx":
		if n := zipWords(path, "word/document.xml"); n > 0 {
			return countNoun(n, "word")
		}
	case "md", "txt":
		if n := textWords(path); n > 0 {
			return countNoun(n, "word")
		}
	case "csv", "tsv":
		if n := textLines(path); n > 1 {
			return countNoun(n-1, "row")
		}
	}
	return ""
}

func openZip(path string) (*zip.ReadCloser, bool) {
	info, err := os.Stat(path)
	if err != nil || info.Size() > 256<<20 {
		return nil, false
	}
	z, err := zip.OpenReader(path)
	if err != nil {
		return nil, false
	}
	return z, true
}

func zipEntry(z *zip.ReadCloser, name string) io.ReadCloser {
	for _, f := range z.File {
		if f.Name == name {
			r, err := f.Open()
			if err != nil {
				return nil
			}
			return r
		}
	}
	return nil
}

// peekWorkbook: "3 sheets: Register (8 rows), Clauses (64 rows), Summary".
func peekWorkbook(path string) string {
	z, ok := openZip(path)
	if !ok {
		return ""
	}
	defer z.Close()

	type sheet struct{ name, target string }
	var sheets []sheet
	if r := zipEntry(z, "xl/workbook.xml"); r != nil {
		dec := xml.NewDecoder(io.LimitReader(r, peekMaxBytes))
		for {
			tok, err := dec.Token()
			if err != nil {
				break
			}
			if se, ok := tok.(xml.StartElement); ok && se.Name.Local == "sheet" {
				var s sheet
				for _, a := range se.Attr {
					switch {
					case a.Name.Local == "name":
						s.name = a.Value
					case a.Name.Local == "id":
						s.target = a.Value
					}
				}
				sheets = append(sheets, s)
			}
		}
		r.Close()
	}
	if len(sheets) == 0 {
		return ""
	}
	targets := map[string]string{}
	if r := zipEntry(z, "xl/_rels/workbook.xml.rels"); r != nil {
		dec := xml.NewDecoder(io.LimitReader(r, peekMaxBytes))
		for {
			tok, err := dec.Token()
			if err != nil {
				break
			}
			if se, ok := tok.(xml.StartElement); ok && se.Name.Local == "Relationship" {
				var id, target string
				for _, a := range se.Attr {
					switch a.Name.Local {
					case "Id":
						id = a.Value
					case "Target":
						target = a.Value
					}
				}
				targets[id] = strings.TrimPrefix(strings.TrimPrefix(target, "/xl/"), "/")
			}
		}
		r.Close()
	}
	var parts []string
	for i, s := range sheets {
		if i == 3 {
			parts = append(parts, fmt.Sprintf("+%d more", len(sheets)-3))
			break
		}
		label := clean(s.name)
		if t := targets[s.target]; t != "" {
			if rows := sheetRows(z, "xl/"+strings.TrimPrefix(t, "xl/")); rows > 0 {
				label += " (" + countNoun(rows, "row") + ")"
			}
		}
		parts = append(parts, label)
	}
	return countNoun(len(sheets), "sheet") + ": " + strings.Join(parts, ", ")
}

// sheetRows reads a worksheet's declared extent (<dimension ref="A1:H1205">),
// which every writer puts at the top, so only the head of the sheet is read.
func sheetRows(z *zip.ReadCloser, name string) int {
	r := zipEntry(z, name)
	if r == nil {
		return 0
	}
	defer r.Close()
	dec := xml.NewDecoder(io.LimitReader(r, 64<<10))
	for {
		tok, err := dec.Token()
		if err != nil {
			return 0
		}
		se, ok := tok.(xml.StartElement)
		if !ok {
			continue
		}
		switch se.Name.Local {
		case "dimension":
			for _, a := range se.Attr {
				if a.Name.Local == "ref" {
					return lastRow(a.Value)
				}
			}
			return 0
		case "sheetData":
			return 0
		}
	}
}

var cellRowRe = regexp.MustCompile(`(\d+)$`)

func lastRow(ref string) int {
	parts := strings.Split(ref, ":")
	m := cellRowRe.FindStringSubmatch(parts[len(parts)-1])
	if m == nil {
		return 0
	}
	n, _ := strconv.Atoi(m[1])
	return n
}

var slideRe = regexp.MustCompile(`^ppt/slides/slide\d+\.xml$`)

func peekDeck(path string) string {
	z, ok := openZip(path)
	if !ok {
		return ""
	}
	defer z.Close()
	n := 0
	for _, f := range z.File {
		if slideRe.MatchString(f.Name) {
			n++
		}
	}
	if n == 0 {
		return ""
	}
	return countNoun(n, "slide")
}

// zipWords counts the words in a docx body's text runs.
func zipWords(path, entry string) int {
	z, ok := openZip(path)
	if !ok {
		return 0
	}
	defer z.Close()
	r := zipEntry(z, entry)
	if r == nil {
		return 0
	}
	defer r.Close()
	dec := xml.NewDecoder(io.LimitReader(r, peekMaxBytes))
	words, inText := 0, false
	for {
		tok, err := dec.Token()
		if err != nil {
			return words
		}
		switch t := tok.(type) {
		case xml.StartElement:
			inText = t.Name.Local == "t"
		case xml.EndElement:
			inText = false
		case xml.CharData:
			if inText {
				words += len(strings.Fields(string(t)))
			}
		}
	}
}

func textWords(path string) int {
	f, err := os.Open(path) // #nosec G304 -- a file this turn wrote, peeked to describe it
	if err != nil {
		return 0
	}
	defer f.Close()
	sc := bufio.NewScanner(io.LimitReader(f, peekMaxBytes))
	sc.Split(bufio.ScanWords)
	n := 0
	for sc.Scan() {
		n++
	}
	return n
}

func textLines(path string) int {
	f, err := os.Open(path) // #nosec G304 -- a file this turn wrote, peeked to describe it
	if err != nil {
		return 0
	}
	defer f.Close()
	sc := bufio.NewScanner(io.LimitReader(f, peekMaxBytes))
	sc.Buffer(make([]byte, 64<<10), 1<<20)
	n := 0
	for sc.Scan() {
		n++
	}
	return n
}

// --- opening -----------------------------------------------------------------

// lastDeliverable is the newest openable file any answer produced.
func (m ChatModel) lastDeliverable() (deliverable, bool) {
	for i := len(m.messages) - 1; i >= 0; i-- {
		for _, d := range m.messages[i].Deliverables {
			if openableExts[extOf(d.Path)] {
				return d, true
			}
		}
	}
	return deliverable{}, false
}

type fileOpenResultMsg struct {
	path string
	err  error
}

// openFile hands a deliverable to the platform's opener. Documents only — see
// openableExts — and only a path this session saw a turn produce.
func openFile(path string) tea.Cmd {
	return func() tea.Msg {
		if !openableExts[extOf(path)] {
			return fileOpenResultMsg{path: path, err: fmt.Errorf("only documents open from here")}
		}
		var cmd *exec.Cmd
		switch runtime.GOOS {
		case "darwin":
			cmd = exec.Command("open", path)
		case "windows":
			cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
		default:
			cmd = exec.Command("xdg-open", path)
		}
		if err := cmd.Start(); err != nil {
			return fileOpenResultMsg{path: path, err: err}
		}
		go func() { _ = cmd.Wait() }()
		return fileOpenResultMsg{path: path}
	}
}
