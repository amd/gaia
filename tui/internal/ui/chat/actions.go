// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
)

// Every action line leads with WHAT it does, in plain words. A shell command or
// a page of Python is the evidence, not the headline: it is kept for Ctrl+O and
// shown folded as one dim line at most.
//
// The words come from, best first: a `description` the model wrote with the
// call, the goal the agent announced before it, and a summary derived here from
// the tool and its parsed arguments. Never a raw dump.

// Action categories. A category decides the derived summary, how runs of calls
// fold ("Read 3 files"), and the live status verb.
const (
	catShell  = "shell"
	catPython = "python"
	catRead   = "read"
	catWrite  = "write"
	catEdit   = "edit"
	catWeb    = "web"
	catFetch  = "fetch"
	catDocs   = "docs"
	catList   = "list"
	catSkill  = "skill"
	catOther  = "other"
)

// descriptionMax is the widest a model-written description may be. It is a
// one-line headline; anything longer is prose that belongs in the answer.
const descriptionMax = 80

// commandKeepLines bounds how much of a command or script is carried for
// Ctrl+O. The full text is in the trace and the agent log.
const commandKeepLines = 12

// changeKeepLines bounds the lines of a write or edit kept for its diff.
const changeKeepLines = 40

func categoryOf(tool string) string {
	switch {
	case shellTools[tool]:
		return catShell
	}
	switch tool {
	case "run_python", "execute_python", "python", "execute_python_file", "run_python_file":
		return catPython
	case "read_file", "get_file_preview", "read_document", "read_pdf":
		return catRead
	case "write_file", "create_file", "save_file":
		return catWrite
	case "edit_file", "replace_in_file", "apply_patch":
		return catEdit
	case "web_search", "search_web":
		return catWeb
	case "fetch_page", "fetch_url", "download_file":
		return catFetch
	case "query_documents", "search_documents", "query_specific_file", "search_indexed_chunks", "index_document":
		return catDocs
	case "list_directory", "search_file", "find_files", "tree", "browse_directory", "list_dir":
		return catList
	case "load_skill", "unload_skill", "install_skill", "list_skills", "search_skill_hub", "skill_status", "remove_skill":
		return catSkill
	}
	return catOther
}

// callInfo is what one tool call says about itself, parsed once when it arrives.
type callInfo struct {
	Category    string
	Description string
	Command     string
	Derived     string
	Reads       []string
	Writes      []string
	Change      *fileChange
}

// fileChange is a write or an edit, bounded, for the compact diff.
type fileChange struct {
	Path     string
	Old, New []string
	OldTotal int
	NewTotal int
	Created  bool
	// StartLine is the 1-based line the change starts at, 0 when unknown.
	StartLine int
	// Shared is how many leading lines the two sides of an edit had in common
	// and were trimmed; locateEdit adds it back when it finds the edit.
	Shared int
}

// describeCall parses a tool call into the words the work log shows.
func describeCall(tool string, args json.RawMessage, narration string) callInfo {
	obj := decodeObject(args)
	info := callInfo{Category: categoryOf(tool)}
	info.Description = cleanDescription(firstStringOf(obj, "description", "purpose"))

	switch info.Category {
	case catShell:
		cmd := rawString(obj, "command", "cmd")
		info.Command = keepLines(cmd, commandKeepLines)
		info.Derived = deriveShell(cmd)
	case catPython:
		if code := rawString(obj, "code", "source", "script"); code != "" {
			info.Command = keepLines(code, commandKeepLines)
			info.Reads, info.Writes = scriptFiles(code)
			info.Derived = derivePython(code, info.Reads, info.Writes)
		} else if path := rawString(obj, "file_path", "path", "script_path"); path != "" {
			info.Command = "python " + scrubLine(path)
			info.Derived = "Run " + baseName(path)
		}
	case catRead:
		if p := rawString(obj, "file_path", "path", "file"); p != "" {
			info.Reads = []string{p}
			info.Derived = "Read " + baseName(p)
		}
	case catWrite:
		p := rawString(obj, "file_path", "path", "file")
		content := rawString(obj, "content", "text")
		if p != "" {
			info.Writes = []string{p}
			lines := splitLines(content)
			info.Change = &fileChange{
				Path: p, New: headLines(lines, changeKeepLines), NewTotal: len(lines),
				Created: true, StartLine: 1,
			}
			info.Derived = "Write " + baseName(p)
		}
	case catEdit:
		p := rawString(obj, "file_path", "path", "file")
		oldText := rawString(obj, "old_content", "old_string", "old_text", "search")
		newText := rawString(obj, "new_content", "new_string", "new_text", "replace")
		if p != "" {
			info.Writes = []string{p}
			info.Derived = "Edit " + baseName(p)
			if oldText != "" || newText != "" {
				info.Change = newEditChange(p, oldText, newText)
			}
		}
	case catList:
		if p := rawString(obj, "path", "directory", "dir"); p != "" {
			info.Derived = "Look through " + baseName(p)
		}
	case catWeb:
		if q := rawString(obj, "query", "q"); q != "" {
			info.Derived = "Search the web for " + quoted(q, 48)
		}
	case catFetch:
		if u := rawString(obj, "url"); u != "" {
			verb := "Read "
			if tool == "download_file" {
				verb = "Download "
			}
			info.Derived = verb + shortURL(u)
		}
	}
	// A sidecar that narrates what the call is FOR knows more than any
	// derivation; one that only echoes "<label>: <raw argument>" knows less.
	if n := clean(narration); n != "" && !isArgEcho(n, obj) {
		info.Derived = imperative(unclipArg(n, args))
	}
	if info.Derived == "" {
		info.Derived = imperative(narrationFor(tool, args, narration))
	}
	info.Derived = truncateRunes(shortenPaths(clean(info.Derived)), narrationMax)
	return info
}

// isArgEcho reports whether a narration is only a label plus one of the call's
// raw arguments ("Running command: python -m pip …", "Reading file: C:\…") —
// the shape the gaia sidecar sends, which says less than a derived summary.
func isArgEcho(narration string, obj map[string]interface{}) bool {
	_, tail, ok := strings.Cut(narration, ": ")
	if !ok {
		return false
	}
	tail = strings.TrimSuffix(tail, "…")
	if tail == "" {
		return true
	}
	for _, v := range obj {
		if s := clean(stringOf(v)); s != "" && strings.HasPrefix(s, tail) {
			return true
		}
	}
	return false
}

// newEditChange trims the lines the two sides share, so the diff shows only what
// changed.
func newEditChange(path, oldText, newText string) *fileChange {
	oldLines, newLines := splitLines(oldText), splitLines(newText)
	prefix := 0
	for prefix < len(oldLines) && prefix < len(newLines) && oldLines[prefix] == newLines[prefix] {
		prefix++
	}
	suffix := 0
	for suffix < len(oldLines)-prefix && suffix < len(newLines)-prefix &&
		oldLines[len(oldLines)-1-suffix] == newLines[len(newLines)-1-suffix] {
		suffix++
	}
	oldLines = oldLines[prefix : len(oldLines)-suffix]
	newLines = newLines[prefix : len(newLines)-suffix]
	return &fileChange{
		Path: path, Old: headLines(oldLines, changeKeepLines), New: headLines(newLines, changeKeepLines),
		OldTotal: len(oldLines), NewTotal: len(newLines),
		Shared: prefix,
	}
}

// cleanDescription makes a model-written description safe to show, or "" when it
// is not one: empty, a template placeholder, or prose too long to be a headline.
func cleanDescription(s string) string {
	s = clean(scrubDisplayControls(s))
	if s == "" || isTemplateGoal(s) {
		return ""
	}
	s = strings.TrimRight(s, ".")
	return truncateRunes(s, descriptionMax)
}

// templateGoals are the placeholder goals the agent's own prompt and plan runner
// emit. They describe the harness, never the work, and are never shown.
var templateGoals = map[string]bool{
	"objective": true, "achieved": true, "reasoning": true, "goal": true,
	"...": true, "…": true, "n/a": true, "none": true, "null": true,
	"thinking": true, "done": true, "complete": true, "completed": true,
	"plan": true, "response to user": true, "handle empty response error": true,
	"use the object name": true, "following the plan": true, "description": true,
}

var templateGoalPrefixes = []string{
	"following the plan to",
	"executing step",
	"plan:",
	"tool call failed, retrying",
	"step ",
}

// isTemplateGoal reports whether s is placeholder text rather than a real goal.
func isTemplateGoal(s string) bool {
	low := strings.ToLower(strings.TrimSpace(s))
	low = strings.TrimRight(low, ".…")
	if low == "" || templateGoals[low] {
		return true
	}
	for _, p := range templateGoalPrefixes {
		if strings.HasPrefix(low, p) {
			return true
		}
	}
	return strings.Contains(low, "clear statement of") ||
		strings.Contains(low, "what you're trying to achieve") ||
		strings.Contains(low, "what you are trying to achieve")
}

// rawString is the first of keys present in obj as a string, NOT flattened to
// one line — commands and code keep their newlines for the expanded view.
func rawString(obj map[string]interface{}, keys ...string) string {
	for _, k := range keys {
		if s, ok := obj[k].(string); ok && strings.TrimSpace(s) != "" {
			return s
		}
	}
	return ""
}

// keepLines bounds multi-line text for display: control bytes out, at most n
// non-blank-trailing lines, each line scrubbed on its own.
func keepLines(s string, n int) string {
	lines := splitLines(s)
	out := make([]string, 0, min(len(lines), n))
	for _, l := range headLines(lines, n) {
		out = append(out, scrubLine(l))
	}
	return strings.Join(out, "\n")
}

// scrubLine strips escapes and controls from one line but keeps its indentation.
func scrubLine(s string) string {
	s = scrubDisplayControls(s)
	indent := len(s) - len(strings.TrimLeft(s, " \t"))
	lead := strings.ReplaceAll(s[:indent], "\t", "    ")
	return lead + clean(s[indent:])
}

func splitLines(s string) []string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.TrimRight(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

func headLines(lines []string, n int) []string {
	if len(lines) > n {
		return lines[:n]
	}
	return lines
}

// baseName is a path's last element, for either separator.
func baseName(p string) string {
	p = strings.TrimRight(clean(p), `/\`)
	if i := strings.LastIndexAny(p, `/\`); i >= 0 {
		p = p[i+1:]
	}
	return truncateRunes(p, pathMax)
}

func quoted(s string, limit int) string {
	return "'" + truncateRunes(clean(s), limit) + "'"
}

// shortURL is a URL as host plus a clipped path — never the query string.
func shortURL(raw string) string {
	u, err := url.Parse(clean(raw))
	if err != nil || u.Host == "" {
		return truncateRunes(clean(raw), 48)
	}
	host := strings.TrimPrefix(u.Host, "www.")
	path := strings.TrimRight(u.Path, "/")
	if path == "" {
		return host
	}
	return truncateRunes(host+path, 48)
}

func countNoun(n int, noun string) string {
	if n == 1 {
		return "1 " + noun
	}
	return fmt.Sprintf("%s %ss", commas(n), noun)
}

// imperativeForms turns a progressive phrase's first word into the imperative,
// so every headline reads the same way whatever produced it.
var imperativeForms = func() map[string]string {
	m := map[string]string{
		"Running": "Run", "Reading": "Read", "Writing": "Write", "Searching": "Search",
		"Checking": "Check", "Browsing": "Browse", "Looking": "Look", "Opening": "Open",
		"Asking": "Ask", "Querying": "Query", "Previewing": "Preview", "Fetching": "Fetch",
		"Getting": "Get", "Loading": "Load", "Listing": "List", "Editing": "Edit",
		"Creating": "Create", "Installing": "Install", "Removing": "Remove",
		"Unloading": "Unload", "Indexing": "Index", "Downloading": "Download",
		"Scanning": "Scan", "Triaging": "Triage", "Executing": "Run",
		"Making": "Make", "Computing": "Compute", "Comparing": "Compare",
		"Calculating": "Calculate", "Preparing": "Prepare", "Parsing": "Parse",
		"Merging": "Merge", "Exporting": "Export", "Drafting": "Draft",
		"Verifying": "Verify", "Validating": "Validate", "Cleaning": "Clean",
		"Formatting": "Format", "Sorting": "Sort", "Filtering": "Filter",
		"Building": "Build", "Testing": "Test", "Fixing": "Fix", "Renaming": "Rename",
		"Moving": "Move", "Copying": "Copy", "Deleting": "Delete", "Sending": "Send",
	}
	for verb, participle := range verbForms {
		if _, ok := m[participle]; !ok {
			m[participle] = strings.ToUpper(verb[:1]) + verb[1:]
		}
	}
	return m
}()

// imperative rewrites "Reading file: x" as "Read file: x".
func imperative(s string) string {
	first, rest, found := strings.Cut(s, " ")
	if v, ok := imperativeForms[first]; ok {
		if found {
			return v + " " + rest
		}
		return v
	}
	return s
}

// --- shell -----------------------------------------------------------------

// splitShell cuts a command line at &&, ||, ; and | — outside quotes only, so
// a jq filter or a quoted argument with a pipe in it stays one segment.
func splitShell(cmd string) []string {
	var out []string
	var cur strings.Builder
	var quote rune
	runes := []rune(cmd)
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			}
			cur.WriteRune(r)
		case r == '"' || r == '\'':
			quote = r
			cur.WriteRune(r)
		case r == ';' || r == '|' || (r == '&' && i+1 < len(runes) && runes[i+1] == '&'):
			if (r == '|' || r == '&') && i+1 < len(runes) && runes[i+1] == r {
				i++
			}
			if seg := strings.TrimSpace(cur.String()); seg != "" {
				out = append(out, seg)
			}
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	if seg := strings.TrimSpace(cur.String()); seg != "" {
		out = append(out, seg)
	}
	return out
}

// deriveShell says what a shell command does, from its program and arguments.
func deriveShell(cmd string) string {
	cmd = clean(cmd)
	if cmd == "" {
		return "Run a command"
	}
	parts := splitShell(cmd)
	if len(parts) == 0 {
		return "Run a command"
	}
	// `cd somewhere && real-work` is about the work.
	first := parts[0]
	if prog, _ := program(first); (prog == "cd" || prog == "pushd") && len(parts) > 1 {
		parts = parts[1:]
		first = parts[0]
	}
	out := deriveShellSegment(first)
	if extra := len(parts) - 1; extra > 0 {
		out += fmt.Sprintf(" (+%d more)", extra)
	}
	return out
}

// program is a command segment's program name (no path, no .exe) and its args.
func program(segment string) (string, []string) {
	fields := strings.Fields(segment)
	// Leading VAR=value assignments set the environment, they are not the program.
	for len(fields) > 0 && strings.Contains(fields[0], "=") && !strings.HasPrefix(fields[0], "-") {
		fields = fields[1:]
	}
	if len(fields) == 0 {
		return "", nil
	}
	prog := strings.ToLower(baseName(strings.Trim(fields[0], `"'`)))
	prog = strings.TrimSuffix(strings.TrimSuffix(prog, ".exe"), ".cmd")
	return prog, fields[1:]
}

func deriveShellSegment(segment string) string {
	prog, args := program(segment)
	positional := func() []string {
		var out []string
		for _, a := range args {
			if !strings.HasPrefix(a, "-") {
				out = append(out, strings.Trim(a, `"'`))
			}
		}
		return out
	}
	firstArg := func() string {
		if p := positional(); len(p) > 0 {
			return p[0]
		}
		return ""
	}
	switch prog {
	case "python", "python3", "py":
		if len(args) >= 2 && args[0] == "-m" {
			if args[1] == "pip" && len(args) > 2 && args[2] == "install" {
				return installSummary(args[3:], "pip")
			}
			if args[1] == "pytest" {
				return "Run the tests"
			}
			return "Run the " + args[1] + " module"
		}
		if len(args) > 0 && args[0] == "-c" {
			return "Run a Python one-liner"
		}
		if a := firstArg(); a != "" {
			return "Run " + baseName(a)
		}
		return "Start Python"
	case "pip", "pip3", "uv":
		rest := args
		if prog == "uv" && len(rest) > 0 && rest[0] == "pip" {
			rest = rest[1:]
		}
		if len(rest) > 0 && rest[0] == "install" {
			return installSummary(rest[1:], prog)
		}
		return "Run " + prog + " " + strings.Join(headLines(rest, 1), "")
	case "pytest":
		return "Run the tests"
	case "git", "npm", "npx", "go", "cargo", "make", "docker", "gh", "node", "yarn", "pnpm":
		if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
			return "Run " + prog + " " + truncateRunes(strings.Trim(args[0], `"'`), 24)
		}
		return "Run " + prog
	case "ls", "dir", "tree", "get-childitem", "gci":
		if a := firstArg(); a != "" {
			return "List " + baseName(a)
		}
		return "List the folder"
	case "cat", "type", "head", "tail", "more", "less", "get-content", "gc":
		if a := firstArg(); a != "" {
			return "Read " + baseName(a)
		}
		return "Read a file"
	case "grep", "rg", "findstr", "select-string", "ag":
		if a := firstArg(); a != "" {
			return "Search for " + quoted(a, 32)
		}
		return "Search files"
	case "find", "where", "which", "fd":
		return "Find files"
	case "mkdir", "md", "new-item":
		if a := firstArg(); a != "" {
			return "Create " + baseName(a)
		}
		return "Create a folder"
	case "rm", "del", "rmdir", "rd", "remove-item":
		if a := firstArg(); a != "" {
			return "Delete " + baseName(a)
		}
		return "Delete files"
	case "cp", "copy", "copy-item", "xcopy", "robocopy":
		return "Copy files"
	case "mv", "move", "move-item", "ren", "rename":
		return "Move files"
	case "curl", "wget", "invoke-webrequest", "iwr":
		for _, a := range positional() {
			if strings.HasPrefix(a, "http") {
				return "Download from " + shortURL(a)
			}
		}
		return "Download a file"
	case "unzip", "tar", "7z", "expand-archive":
		return "Unpack an archive"
	case "echo", "printf", "write-output":
		return "Print text"
	case "":
		return "Run a command"
	}
	return "Run " + truncateRunes(prog, 24)
}

func installSummary(args []string, via string) string {
	var pkgs []string
	for _, a := range args {
		if !strings.HasPrefix(a, "-") {
			pkgs = append(pkgs, strings.Trim(a, `"'`))
		}
	}
	if len(pkgs) == 0 {
		return "Install packages (" + via + ")"
	}
	if len(pkgs) > 3 {
		return fmt.Sprintf("Install %s and %d more (%s)", strings.Join(pkgs[:2], ", "), len(pkgs)-2, via)
	}
	return "Install " + strings.Join(pkgs, ", ") + " (" + via + ")"
}

// --- python ----------------------------------------------------------------

// pyFileLiteral finds a quoted path with a file extension, plus the code just
// before it — which call it was handed to decides read or write.
var pyFileLiteral = regexp.MustCompile(`(?:[rRbBfFuU]{0,2})(['"])([^'"\n]{1,260}\.[A-Za-z0-9]{1,5})['"]`)

var pyWriteHints = regexp.MustCompile(`(?:to_excel|to_csv|to_json|to_parquet|to_markdown|to_html|\.save|savefig|ExcelWriter|write_text|write_bytes|copyfile|copy2?|move|Workbook\(\)\.save)\s*\(\s*$`)
var pyReadHints = regexp.MustCompile(`(?:read_excel|read_csv|read_json|read_parquet|read_table|load_workbook|Document|Presentation|PdfReader|pdfplumber\.open|ZipFile|read_text|read_bytes|Image\.open|glob(?:\.glob)?|listdir|exists|isfile)\s*\(\s*$`)
var pyOpenMode = regexp.MustCompile(`^\s*,\s*(?:mode\s*=\s*)?['"]([rwaxb+t]+)['"]`)

// scriptFiles names the files a Python snippet visibly reads and writes. Paths
// built at run time are invisible here, and that is fine: the summary then just
// says how long the script is.
func scriptFiles(code string) (reads, writes []string) {
	seenR, seenW := map[string]bool{}, map[string]bool{}
	for _, loc := range pyFileLiteral.FindAllStringSubmatchIndex(code, -1) {
		path := code[loc[4]:loc[5]]
		if strings.ContainsAny(path, "{}%") || strings.HasPrefix(path, ".") && !strings.ContainsAny(path, `/\`) {
			continue // a format template, or a bare extension like ".xlsx"
		}
		before := code[max(0, loc[0]-48):loc[0]]
		after := code[loc[1]:min(len(code), loc[1]+24)]
		isOpen := strings.HasSuffix(strings.TrimRight(before, " "), "open(")
		switch {
		case pyWriteHints.MatchString(before):
			addOnce(&writes, seenW, path)
		case isOpen:
			if m := pyOpenMode.FindStringSubmatch(after); m != nil && strings.ContainsAny(m[1], "wax+") {
				addOnce(&writes, seenW, path)
			} else {
				addOnce(&reads, seenR, path)
			}
		case pyReadHints.MatchString(before):
			addOnce(&reads, seenR, path)
		}
	}
	return reads, writes
}

func addOnce(list *[]string, seen map[string]bool, v string) {
	if !seen[v] {
		seen[v] = true
		*list = append(*list, v)
	}
}

// derivePython: "Run a 42-line Python script that reads 3 spreadsheets and
// writes flights.xlsx".
func derivePython(code string, reads, writes []string) string {
	n := 0
	for _, l := range splitLines(code) {
		if t := strings.TrimSpace(l); t != "" && !strings.HasPrefix(t, "#") {
			n++
		}
	}
	head := "Run a Python one-liner"
	if n > 1 {
		head = fmt.Sprintf("Run a %d-line Python script", n)
	}
	var clauses []string
	if len(reads) > 0 {
		clauses = append(clauses, "reads "+filesPhrase(reads))
	}
	if len(writes) > 0 {
		clauses = append(clauses, "writes "+filesPhrase(writes))
	}
	if len(clauses) == 0 {
		return head
	}
	return head + " that " + strings.Join(clauses, " and ")
}

// filesPhrase names one file, or counts several by kind ("3 spreadsheets").
func filesPhrase(paths []string) string {
	if len(paths) == 1 {
		return baseName(paths[0])
	}
	kind := fileKind(paths[0])
	for _, p := range paths[1:] {
		if fileKind(p) != kind {
			kind = "file"
			break
		}
	}
	return fmt.Sprintf("%d %ss", len(paths), kind)
}

// fileKind is the noun a person uses for a file of this type.
func fileKind(path string) string {
	switch strings.ToLower(strings.TrimPrefix(filepath.Ext(baseName(path)), ".")) {
	case "xlsx", "xls", "xlsm", "csv", "tsv", "ods":
		return "spreadsheet"
	case "docx", "doc", "odt", "rtf":
		return "document"
	case "pptx", "ppt", "odp":
		return "deck"
	case "pdf":
		return "PDF"
	case "md", "txt":
		return "text file"
	case "png", "jpg", "jpeg", "gif", "svg", "webp":
		return "image"
	case "zip", "gz", "tar", "7z":
		return "archive"
	case "py", "js", "ts", "go", "rs", "java", "c", "cpp", "sh", "ps1":
		return "source file"
	}
	return "file"
}

// categoryVerb is the live status line's verb while an action of cat runs.
func categoryVerb(cat string) string {
	switch cat {
	case catShell:
		return "Running a command"
	case catPython:
		return "Running a Python script"
	case catRead:
		return "Reading"
	case catWrite:
		return "Writing a file"
	case catEdit:
		return "Editing a file"
	case catWeb:
		return "Searching the web"
	case catFetch:
		return "Reading a web page"
	case catDocs:
		return "Searching your documents"
	case catList:
		return "Looking through folders"
	case catSkill:
		return "Setting up a skill"
	}
	return "Working"
}

// filesTouched names up to three distinct files a run of calls touched —
// "Edited dates.py, test_dates.py x3" — so a count never claims one file was
// changed three times, or three files when it was two. More than three are
// counted.
func filesTouched(verb string, n int, items []ActivityItem) string {
	var names []string
	seen := map[string]bool{}
	for _, it := range items {
		paths := append(append([]string(nil), it.Call.Reads...), it.Call.Writes...)
		if len(paths) == 0 {
			if _, target, ok := strings.Cut(clean(it.Content), ": "); ok {
				paths = []string{target}
			}
		}
		for _, p := range paths {
			if b := baseName(p); b != "" && !seen[b] {
				seen[b] = true
				names = append(names, b)
			}
		}
	}
	switch {
	case len(names) == 0 || len(names) > 3:
		return verb + " " + countNoun(max(n, len(names)), "file")
	case len(names) < n:
		return fmt.Sprintf("%s %s x%d", verb, strings.Join(names, ", "), n)
	}
	return verb + " " + strings.Join(names, ", ")
}

// groupSummary is a folded run of derived-headline calls of one category:
// "Read 3 files", "Ran 2 commands". Past tense, because the run is history.
func groupSummary(cat string, n int, items []ActivityItem) string {
	switch cat {
	case catShell:
		return "Ran " + countNoun(n, "command")
	case catPython:
		return "Ran " + countNoun(n, "Python script")
	case catRead:
		return filesTouched("Read", n, items)
	case catWrite:
		return filesTouched("Wrote", n, items)
	case catEdit:
		return filesTouched("Edited", n, items)
	case catWeb:
		return fmt.Sprintf("Searched the web %d times", n)
	case catFetch:
		return "Read " + countNoun(n, "web page")
	case catDocs:
		return fmt.Sprintf("Searched your documents %d times", n)
	case catList:
		return "Looked through " + countNoun(n, "folder")
	}
	return ""
}
