// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// The work log is the only thing standing between a user and a blank screen for
// 60-120 seconds, so every line has to answer "what is it doing for ME" — not
// "what is the harness doing". Two rules follow from that:
//
//   - A tool call becomes an imperative phrase plus the ONE argument that
//     matters. `run_shell_command` shows the command itself, because the command
//     is the highest-signal thing a person can be shown.
//   - A tool result becomes ONE indented line: outcome, size, latency. Never raw
//     JSON — the render card in the transcript is where a full result belongs.
const (
	// widestLogMeasure is an upper BOUND on what a work-log row could ever be
	// laid out to — never the measure a row actually uses, which is logWidth
	// and has no ceiling at all. It exists only because capture happens before
	// any width is known, and the user can widen the window mid-turn, so the
	// bounds below have to assume the roomiest window they might later be
	// rendered into.
	//
	// 500 is the widest the TUI accepts anywhere (control/server.go's resize
	// range), so deriving from it means no window this program will lay out
	// can outrun what was captured for it.
	widestLogMeasure = 500
	// narrationMax bounds the whole narration string. It is a sanity bound on a
	// runaway payload — a shell command with a page-long --jq expression — set to
	// the rows the log can show on the WIDEST window, so nothing is carried that
	// no window could ever render. A narrower one shows less; it is the same
	// string, laid out to fewer columns.
	narrationMax = logHeadRows * widestLogMeasure
	// detailMax bounds an outcome string the same way, over the rows its `└`
	// line may wrap to. An outcome that failed carries the remedy, so it gets
	// the same room as the call above it rather than one clipped row.
	detailMax = logDetailRows * widestLogMeasure
)

// shellTools name their argument better than any phrase could: for these the
// command IS the narration.
var shellTools = map[string]bool{
	"run_shell_command": true,
	"run_command":       true,
	"execute_command":   true,
	"shell":             true,
	"bash":              true,
	"terminal":          true,
}

// toolPhrase is how one tool reads in the work log. With is used when the
// salient argument is present (%s is that argument), Without when it is not,
// and Arg names the argument key to prefer over the generic ranking below.
type toolPhrase struct {
	With    string
	Without string
	Arg     string
}

// toolPhrases covers the tools a GAIA user actually watches run. Everything else
// falls through to verb derivation — a curated table that has to be exhaustive
// is a table that silently rots, so this one only has to be better than the
// derivation for the tools people see most.
var toolPhrases = map[string]toolPhrase{
	// Skill library (the flagship agent's own tools)
	"list_skills":      {Without: "Checking your installed skills"},
	"skill_status":     {Without: "Checking which skills are active"},
	"search_skill_hub": {With: "Searching the skill hub for %s", Without: "Browsing the skill hub", Arg: "query"},
	"install_skill":    {With: "Installing the %s skill", Without: "Installing a skill", Arg: "name"},
	"remove_skill":     {With: "Removing the %s skill", Without: "Removing a skill", Arg: "name"},
	"load_skill":       {With: "Loading the %s skill", Without: "Loading a skill", Arg: "name"},
	"unload_skill":     {With: "Unloading the %s skill", Without: "Unloading a skill", Arg: "name"},

	// Documents / RAG
	"query_documents":       {With: "Searching your documents for %s", Without: "Searching your documents", Arg: "query"},
	"search_documents":      {With: "Searching your documents for %s", Without: "Searching your documents", Arg: "query"},
	"query_specific_file":   {With: "Searching %s", Without: "Searching a document", Arg: "file_path"},
	"search_indexed_chunks": {With: "Looking for %s in your documents", Without: "Searching your documents", Arg: "query"},
	"index_document":        {With: "Indexing %s", Without: "Indexing a document", Arg: "file_path"},

	// Files
	"read_file":        {With: "Reading %s", Without: "Reading a file", Arg: "file_path"},
	"write_file":       {With: "Writing %s", Without: "Writing a file", Arg: "file_path"},
	"create_file":      {With: "Creating %s", Without: "Creating a file", Arg: "file_path"},
	"edit_file":        {With: "Editing %s", Without: "Editing a file", Arg: "file_path"},
	"get_file_preview": {With: "Previewing %s", Without: "Previewing a file", Arg: "file_path"},
	"list_directory":   {With: "Listing %s", Without: "Listing the current folder", Arg: "directory"},
	"search_file":      {With: "Looking for files matching %s", Without: "Looking for files", Arg: "pattern"},

	// Web
	"web_search":    {With: "Searching the web for %s", Without: "Searching the web", Arg: "query"},
	"search_web":    {With: "Searching the web for %s", Without: "Searching the web", Arg: "query"},
	"fetch_page":    {With: "Reading %s", Without: "Fetching a page", Arg: "url"},
	"fetch_url":     {With: "Reading %s", Without: "Fetching a page", Arg: "url"},
	"download_file": {With: "Downloading %s", Without: "Downloading a file", Arg: "url"},

	// Email
	"pre_scan_inbox":  {Without: "Scanning your inbox"},
	"list_inbox":      {Without: "Reading your inbox"},
	"triage_inbox":    {Without: "Triaging your inbox"},
	"get_message":     {With: "Opening message %s", Without: "Opening a message", Arg: "message_id"},
	"triage_message":  {With: "Triaging message %s", Without: "Triaging a message", Arg: "message_id"},
	"search_messages": {With: "Searching your mail for %s", Without: "Searching your mail", Arg: "query"},
}

// verbForms turns a tool name's leading token into a present-participle phrase,
// so an unlisted `unload_skill_set` still reads as "Unloading skill set".
var verbForms = map[string]string{
	"add": "Adding", "analyze": "Analyzing", "append": "Appending", "apply": "Applying",
	"browse": "Browsing", "build": "Building", "check": "Checking", "classify": "Classifying",
	"convert": "Converting", "count": "Counting", "create": "Creating", "delete": "Deleting",
	"describe": "Describing", "download": "Downloading", "edit": "Editing", "evaluate": "Evaluating",
	"execute": "Running", "extract": "Extracting", "fetch": "Fetching", "find": "Finding",
	"generate": "Generating", "get": "Getting", "index": "Indexing", "insert": "Inserting",
	"inspect": "Inspecting", "install": "Installing", "list": "Listing", "load": "Loading",
	"open": "Opening", "plot": "Plotting", "query": "Querying", "read": "Reading",
	"remove": "Removing", "render": "Rendering", "resolve": "Resolving", "run": "Running",
	"save": "Saving", "scan": "Scanning", "search": "Searching", "send": "Sending",
	"set": "Setting", "show": "Showing", "start": "Starting", "stop": "Stopping",
	"summarize": "Summarizing", "triage": "Triaging", "unload": "Unloading", "update": "Updating",
	"upload": "Uploading", "write": "Writing",
}

// salientArgKeys ranks argument names by how much they tell a watching user.
// A command beats a query beats a path beats a bare name.
var salientArgKeys = []string{
	"command", "cmd", "sql", "query", "q", "url", "file_path", "path",
	"filepath", "file", "filename", "directory", "dir", "pattern",
	"name", "topic", "message_id", "issue", "text", "prompt",
}

// toolNarration is the work-log line for one tool call: what the agent is doing,
// in words the person who typed the request would use.
//
// Order of preference, most trustworthy first: the sidecar's own narration, the
// curated phrase for this tool, a phrase derived from the tool name's verb, and
// finally the raw tool name — which is honest about being a fallback rather than
// inventing a description for a tool this client has never heard of.
func toolNarration(tool string, args json.RawMessage, narration string) string {
	if n := clean(narration); n != "" {
		return truncateRunes(n, narrationMax)
	}

	// The command is the narration — anything wrapped round it is noise.
	if shellTools[tool] {
		if cmd := argValue(args, "command", "cmd"); cmd != "" {
			return truncateRunes(cmd, narrationMax)
		}
	}

	phrase, known := toolPhrases[tool]
	if !known {
		phrase = derivePhrase(tool)
	}

	arg := ""
	if phrase.Arg != "" {
		arg = argValue(args, phrase.Arg)
	}
	if arg == "" {
		arg = salientArg(args)
	}

	line := phrase.Without
	if arg != "" && phrase.With != "" {
		// Budgeted against the TEMPLATE, not against the whole line. Several
		// phrases put words after the %s — "Looking for %s in your documents" —
		// and an argument allowed to fill the line pushed them off the end,
		// leaving the reader the query with no clue what was done with it.
		room := narrationMax - displayWidth(fmt.Sprintf(phrase.With, ""))
		line = fmt.Sprintf(phrase.With, truncateRunes(arg, room))
	} else if arg != "" {
		line = phrase.Without + ": " + truncateRunes(arg, narrationMax-displayWidth(phrase.Without)-2)
	}
	if line == "" {
		line = "Running " + tool
	}
	return truncateRunes(line, narrationMax)
}

// derivePhrase builds a phrase from the tool name when the table has no entry.
// An unrecognised leading verb yields "Running <tool>" rather than a guess:
// "Pre scan inbox" reads like a bug, "Running pre_scan_inbox" reads like a tool.
func derivePhrase(tool string) toolPhrase {
	// The tool name goes into a Sprintf FORMAT string, so a literal "%" in it
	// would be read as a verb: derivePhrase("get_100%_done") produced
	// "Getting 100%!d(string=arg)one". Escaped, never trusted.
	safe := strings.ReplaceAll(tool, "%", "%%")
	parts := strings.Split(tool, "_")
	verb, ok := verbForms[strings.ToLower(parts[0])]
	if !ok {
		return toolPhrase{With: "Running " + safe + ": %s", Without: "Running " + tool}
	}
	if len(parts) == 1 {
		return toolPhrase{With: verb + " %s", Without: verb}
	}
	object := strings.Join(parts[1:], " ")
	return toolPhrase{
		With:    verb + " " + strings.ReplaceAll(object, "%", "%%") + ": %s",
		Without: verb + " " + object,
	}
}

// toolResultDetail is the single `└` line under a tool call: what came back, how
// much of it, and how long it took. One line, always — the transcript's render
// card is where a full result goes.
func toolResultDetail(e event.CanonicalToolResultEvent) string {
	// Classified FIRST, so every return below can carry the word. A preview that
	// short-circuited ahead of this left a failed call marked in red and nothing
	// else — the exact colour-only signal renderActivityItem promises never to
	// rely on.
	failed := !toolResultSucceeded(e.Data)

	if p := clean(e.Preview); p != "" {
		return markFailed(p, failed)
	}

	data := decodeObject(e.Data)
	if data == nil {
		if failed {
			return "failed"
		}
		return ""
	}
	if p := clean(stringOf(data["preview"])); p != "" {
		return markFailed(p, failed)
	}

	var parts []string

	if summary := firstLine(stringOf(data["summary"])); summary != "" && !isBareStatusWord(summary) {
		parts = append(parts, summary)
	}
	if len(parts) == 0 {
		// clean, not firstLine, matching failureDetail: this is the ERROR text
		// path, and a tool puts the remedy on its second line as often as not.
		// `summary` above stays first-line-only — a summary that runs to several
		// lines is a payload dump, not a sentence with a tail worth keeping.
		if msg := firstStringOf(data, "message", "error", "detail", "display_message"); msg != "" {
			parts = append(parts, msg)
		}
	}
	if len(parts) == 0 {
		if c := countPhrase(data); c != "" {
			parts = append(parts, c)
		}
	}
	if tier := clean(stringOf(data["security_tier"])); tier != "" {
		parts = append(parts, tier+" tier")
	}
	if latency := latencyText(data["latency_ms"]); latency != "" {
		parts = append(parts, latency)
	}

	// A result that says nothing at all still has to prove it arrived: silence
	// under a tool line is the exact ambiguity this whole log exists to remove.
	if len(parts) == 0 {
		if failed {
			return "failed"
		}
		return "done"
	}

	return markFailed(strings.Join(parts, " · "), failed)
}

// markFailed puts the outcome's state into the WORDS. Failure has to survive a
// terminal with no colour, so it is never left to failStyle alone.
func markFailed(line string, failed bool) string {
	if failed && !strings.HasPrefix(strings.ToLower(line), "failed") {
		line = "failed — " + line
	}
	return truncateRunes(line, detailMax)
}

// countPhrase names what came back and how much of it — "18 skills", "3 files".
// The collection's own key is the noun, so a new tool returning `issues` reads
// correctly without this list being updated.
func countPhrase(data map[string]interface{}) string {
	for _, key := range []string{
		"skills", "files", "file_list", "documents", "chunks", "results",
		"messages", "issues", "rows", "items", "matches", "entries",
		"registered_tools", "tools",
	} {
		if list, ok := data[key].([]interface{}); ok {
			return fmt.Sprintf("%d %s", len(list), pluralize(strings.ReplaceAll(key, "_", " "), len(list)))
		}
	}
	for _, key := range []string{"count", "total", "num_chunks", "chunk_count"} {
		if n, ok := intOf(data[key]); ok {
			return fmt.Sprintf("%d %s", n, pluralize("items", n))
		}
	}
	return ""
}

// pluralize singularizes a payload key for a count of one. "1 skills" is the
// kind of detail that makes a UI feel machine-generated — and so is "1 matche",
// which a bare TrimSuffix("s") produces from "matches".
func pluralize(noun string, n int) string {
	if n != 1 {
		return noun
	}
	switch {
	case strings.HasSuffix(noun, "ies"):
		return strings.TrimSuffix(noun, "ies") + "y" // entries -> entry
	case strings.HasSuffix(noun, "ches"), strings.HasSuffix(noun, "shes"),
		strings.HasSuffix(noun, "sses"), strings.HasSuffix(noun, "xes"):
		return strings.TrimSuffix(noun, "es") // matches -> match
	case strings.HasSuffix(noun, "s"):
		return strings.TrimSuffix(noun, "s")
	}
	return noun
}

// isBareStatusWord reports whether a summary is the harness's own status echoed
// back. "success" under a tool line tells the user nothing they can't already
// see from the fact that the line closed.
func isBareStatusWord(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "success", "ok", "done", "completed", "error", "failed":
		return true
	}
	return false
}

func latencyText(v interface{}) string {
	ms, ok := floatOf(v)
	if !ok || ms <= 0 {
		return ""
	}
	if ms >= 1000 {
		return fmt.Sprintf("%.1fs", ms/1000)
	}
	return fmt.Sprintf("%dms", int(ms+0.5))
}

// argValue returns the first of keys present in args as a printable scalar.
func argValue(args json.RawMessage, keys ...string) string {
	obj := decodeObject(args)
	if obj == nil {
		return ""
	}
	for _, k := range keys {
		if s := scalarString(obj[k]); s != "" {
			return s
		}
	}
	return ""
}

// salientArg picks the one argument worth showing. Ranked keys first; failing
// that, a lone argument (a single-argument call has no ambiguity about which
// one matters); failing that, nothing — an arbitrary pick from a map is
// non-deterministic and would make the same call render differently each time.
func salientArg(args json.RawMessage) string {
	obj := decodeObject(args)
	if obj == nil {
		return ""
	}
	for _, k := range salientArgKeys {
		if s := scalarString(obj[k]); s != "" {
			return s
		}
	}
	if len(obj) == 1 {
		for _, v := range obj {
			return scalarString(v)
		}
	}
	return ""
}

// decodeObject decodes a JSON object, keeping numbers as json.Number so an id
// like 2924 never renders as 2.924e+03.
func decodeObject(raw json.RawMessage) map[string]interface{} {
	if len(raw) == 0 {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var obj map[string]interface{}
	if err := dec.Decode(&obj); err != nil {
		return nil
	}
	return obj
}

// scalarString renders a printable scalar; a nested object or list returns ""
// so the caller falls through to a phrase instead of pasting JSON into the log.
func scalarString(v interface{}) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return clean(t)
	case json.Number:
		return t.String()
	case bool:
		return fmt.Sprintf("%t", t)
	default:
		return ""
	}
}

func stringOf(v interface{}) string {
	s, _ := v.(string)
	return s
}

func firstStringOf(data map[string]interface{}, keys ...string) string {
	for _, k := range keys {
		if s := clean(stringOf(data[k])); s != "" {
			return s
		}
	}
	return ""
}

func intOf(v interface{}) (int, bool) {
	f, ok := floatOf(v)
	return int(f), ok
}

func floatOf(v interface{}) (float64, bool) {
	switch t := v.(type) {
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case float64:
		return t, true
	case int:
		return float64(t), true
	}
	return 0, false
}

// clean makes an agent-supplied string safe to measure and to print on one row.
//
// Every string this file handles — narration, preview, summary, error text, raw
// argument values — comes off the wire from the agent, and the work log renders
// it through a bare lipgloss style. So the same scrub the card path applies
// (cards.clean, box.go) applies here: ANSI escapes stripped (a payload can carry
// a real ESC — "" is legal JSON) and C0/DEL controls dropped, because they
// have no width but move the cursor, which makes the width math and the terminal
// disagree. Newlines become spaces on top of that: the log has exactly one row
// per event, and a stray newline would push everything below it off screen.
func clean(s string) string {
	if s == "" {
		return s
	}
	if strings.ContainsRune(s, 0x1b) {
		s = ansi.Strip(s)
	}
	s = strings.Map(func(r rune) rune {
		switch {
		case r == '\t' || r == '\n' || r == '\r':
			return ' ' // keep the word break, drop the cursor movement
		case r < 0x20 || r == 0x7f:
			return -1
		}
		return r
	}, s)
	for strings.Contains(s, "  ") {
		s = strings.ReplaceAll(s, "  ", " ")
	}
	return strings.TrimSpace(s)
}

func firstLine(s string) string {
	if i := strings.IndexAny(s, "\r\n"); i >= 0 {
		s = s[:i]
	}
	return clean(s)
}

// truncateRunes cuts to a COLUMN budget, not a rune count.
//
// Both matter and they are different numbers: cutting mid-rune renders a
// replacement box, while counting runes overruns the line for any text that is
// not width-1 — 74 CJK runes occupy 148 columns, which silently doubles the
// live region's real height and shoves the transcript off the pane. ansi.Strip
// first so a caller that already styled its text is measured on what the reader
// actually sees.
func truncateRunes(s string, limit int) string {
	if limit <= 0 {
		return ""
	}
	if ansi.StringWidth(s) <= limit {
		return s
	}
	if limit == 1 {
		return "…"
	}
	var b strings.Builder
	used := 0
	for _, r := range s {
		w := ansi.StringWidth(string(r))
		if used+w > limit-1 {
			break
		}
		b.WriteRune(r)
		used += w
	}
	return strings.TrimRight(b.String(), " ") + "…"
}

// displayWidth is the column count of s, exported to the package for tests and
// for callers budgeting a line.
func displayWidth(s string) int { return ansi.StringWidth(s) }
