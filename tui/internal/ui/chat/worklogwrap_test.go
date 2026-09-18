// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// A turn runs 25-160s and the work log is most of what a user looks at while it
// does. Every line in it was CLIPPED to the pane measure, so the tail of a long
// tool line — the flag that says what the command actually did, the sentence
// that says how to fix a failure — was simply gone. These tests hold the fix:
// the log wraps, and it does so without growing taller than the terminal it is
// drawn on.

// term is one terminal geometry a user actually runs the TUI at.
type term struct {
	name          string
	width, height int
}

var terms = []term{
	{"tiny", 60, 12},
	{"standard", 80, 24},
	{"tall", 100, 40},
	{"wide", 200, 50},
}

// chatAt builds a streaming chat model at one geometry.
func chatAt(t *testing.T, tm term) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = tm.width, tm.height
	m.resize()
	m.streaming = true
	m.queryStart = time.Now()
	return m
}

// longShell is the case that motivated this: one command whose jq expression
// runs past any terminal's width.
const longShell = "gh issue list --repo amd/gaia --state open --limit 50 --json number,title,labels --jq 'map(select(.labels)) | length'"

// longURL is the same problem with no spaces to break on at all.
const longURL = "https://github.com/amd/gaia/blob/main/tui/internal/ui/chat/model.go#L2033-renderLiveRegion-and-the-work-log-height-budget"

func shellCall(cmd string) event.CanonicalToolCallEvent {
	args, _ := json.Marshal(map[string]string{"command": cmd})
	return event.CanonicalToolCallEvent{Type: "tool_call", Tool: "run_shell_command", Args: args}
}

// rowsOf strips styling and returns the region as rows, the way a terminal
// shows it.
func rowsOf(s string) []string { return strings.Split(ansi.Strip(s), "\n") }

// liveClock matches the elapsed timer the live row carries. Stripped before a
// content assertion: it rides at the END of the first row, so a naive join drops
// "0:04" into the middle of the sentence under test.
var liveClock = regexp.MustCompile(` +\d+:\d{2}$`)

// despace compares text by its characters alone, so a row break inside an
// unbroken token does not read as a missing character.
func despace(s string) string { return strings.ReplaceAll(s, " ", "") }

// flat joins the rows back into the one sentence they read as, so a test can
// ask whether text SURVIVED without caring where it broke.
func flat(rows []string) string {
	var parts []string
	for _, r := range rows {
		parts = append(parts, strings.TrimSpace(liveClock.ReplaceAllString(r, "")))
	}
	return strings.TrimSpace(strings.Join(parts, " "))
}

// The tail is the point. A clipped shell command loses the flags that say what
// it did; a clipped failure loses the remedy.
func TestWorkLogWrapsRatherThanClipping(t *testing.T) {
	cases := []struct {
		name string
		feed []interface{}
		want string
	}{
		{
			name: "shell command keeps its flags",
			feed: []interface{}{shellCall(longShell)},
			want: longShell,
		},
		{
			name: "a failure keeps its remedy",
			feed: []interface{}{
				event.CanonicalToolCallEvent{Type: "tool_call", Tool: "write_file"},
				event.CanonicalToolResultEvent{
					Type: "tool_result", Tool: "write_file",
					Preview: "failed - permission denied; run gaia from a shell with write access to that folder",
				},
			},
			want: "run gaia from a shell with write access to that folder",
		},
		{
			// The shape components.WrapText cannot break: one token with no
			// spaces in it. A URL and a Windows path are the lines most likely
			// to run long, and they wrap nowhere on their own.
			name: "an unbroken token is broken by force",
			feed: []interface{}{
				event.CanonicalToolCallEvent{
					Type: "tool_call", Tool: "fetch_page",
					Args: []byte(`{"url":"` + longURL + `"}`),
				},
			},
			want: longURL,
		},
		{
			// The typed-error path (failureDetail), which composes its own line
			// rather than taking the sidecar's preview. It was clipped at 66
			// columns — the narrowest cut in the whole log, on the one line a
			// user has to act on.
			name: "a typed tool error keeps its remedy",
			feed: []interface{}{
				event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_inbox"},
				event.CanonicalToolResultEvent{
					// Render is what puts this on the typed-error path
					// (canonical.go gates the classifier on a declared card).
					Type: "tool_result", Tool: "list_inbox", Render: "email_pre_scan",
					Data: []byte(`{"success":false,"error":{"code":"CONNECTOR_ERROR","message":"the Google connector is not authorised for this mailbox; run gaia connectors login google and try again"}}`),
				},
			},
			want: "run gaia connectors login google and try again",
		},
		{
			// The legacy dialect composes its own line ("tool: arg") at capture
			// time and used to cut the argument at 60 columns, before the
			// renderer ever saw it. Both transports feed the same log, so both
			// have to survive the same wrap.
			name: "the legacy transport keeps its argument",
			feed: []interface{}{
				event.ToolStartEvent{Tool: "run_shell_command"},
				event.ToolArgsEvent{Args: []byte(`{"command":"` + longShell + `"}`)},
			},
			want: longShell,
		},
	}

	// From "standard" up. A 60x12 terminal has two rows for the whole region and
	// genuinely cannot show a wrapped command; that case is its own test
	// (TestWorkLogNeverRendersEmpty — it must MARK the cut, not hide it).
	for _, tc := range cases {
		for _, tm := range terms[1:] {
			t.Run(tc.name+"/"+tm.name, func(t *testing.T) {
				m := feed(t, chatAt(t, tm), tc.feed...)
				rows := rowsOf(m.renderLiveRegion())
				t.Logf("\n%s", strings.Join(rows, "\n"))

				// Compared without spaces: a token with no break in it is
				// hard-broken across rows, so the characters survive in order
				// but a space lands where the row ended. That is the wrap
				// working, not text being lost.
				got := flat(rows)
				if !strings.Contains(despace(got), despace(tc.want)) {
					t.Errorf("the log lost the tail that carries the point.\nwant: %s\ngot:  %s", tc.want, got)
				}
			})
		}
	}
}

// Wrapping multiplies rows per action, so the height budget matters MORE, not
// less: a log that grows without bound shoves the answer being read off the top.
func TestWorkLogStaysWithinItsHeightBudget(t *testing.T) {
	for _, tm := range terms {
		t.Run(tm.name, func(t *testing.T) {
			m := chatAt(t, tm)
			// Every action long enough to wrap, so the budget is under real
			// pressure rather than nominal.
			for i := 0; i < 8; i++ {
				m = feed(t, m,
					// Alternating: prose that WrapText can break, and a token
					// it cannot. The width assertion below only bites on the
					// second kind.
					shellCall(longShell+" "+longURL),
					event.CanonicalToolResultEvent{
						Type: "tool_result", Tool: "run_shell_command",
						Preview: "failed - the remote closed the connection after 30s; check the network and retry",
					},
				)
			}
			rows := rowsOf(m.renderLiveRegion())
			t.Logf("\n%s", strings.Join(rows, "\n"))

			if n, budget := len(rows), m.logRows(); n > budget {
				t.Errorf("live region is %d rows on a %dx%d terminal; budget is %d", n, tm.width, tm.height, budget)
			}
			if n, half := len(rows), m.viewport.Height/2; half > 0 && n > half {
				t.Errorf("live region takes %d of the viewport's %d rows — it is crowding out the transcript", n, m.viewport.Height)
			}
			for _, r := range rows {
				if w := ansi.StringWidth(r); w > tm.width {
					t.Errorf("row is %d columns on a %d-column terminal (the viewport clips it): %q", w, tm.width, r)
				}
			}
		})
	}
}

// On a 12-row terminal the budget is two rows and one wrapped command is three.
// Trimming the newest action away leaves an EMPTY region — a blank screen, the
// one thing this region exists to prevent.
func TestWorkLogNeverRendersEmpty(t *testing.T) {
	m := feed(t, chatAt(t, term{"tiny", 60, 12}), shellCall(strings.Repeat("gaia --flag ", 40)))
	rows := rowsOf(m.renderLiveRegion())
	t.Logf("\n%s", strings.Join(rows, "\n"))

	if flat(rows) == "" {
		t.Fatal("the live region rendered nothing at all")
	}
	if !strings.Contains(flat(rows), "gaia --flag") {
		t.Errorf("the running action is not on screen:\n%s", strings.Join(rows, "\n"))
	}
	if !strings.Contains(flat(rows), "…") {
		t.Errorf("the action was cut to fit with nothing saying so:\n%s", strings.Join(rows, "\n"))
	}
}

// One action must not spend the whole region on itself: the log's job is to
// show a SEQUENCE of work, and a 900-character command that fills the window
// hides everything else the agent did.
func TestOneLongActionCannotFillTheRegion(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_skills"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "list_skills", Preview: "18 skills"},
		shellCall(strings.Repeat("gaia --flag ", 80)),
	)
	rows := rowsOf(m.renderLiveRegion())
	t.Logf("\n%s", strings.Join(rows, "\n"))

	// The long action wrapped rather than being clipped to one row — the
	// property under test, and the half of it that fails against the old
	// truncating renderer.
	wrapped := 0
	for _, r := range rows {
		if strings.Contains(r, "gaia --flag") {
			wrapped++
		}
	}
	if wrapped != logHeadRows {
		t.Errorf("the long command occupies %d rows; it should wrap to exactly the %d-row cap", wrapped, logHeadRows)
	}
	if n := len(rows); n > m.logRows() {
		t.Errorf("one action pushed the region to %d rows, past its %d-row budget", n, m.logRows())
	}
	if !strings.Contains(flat(rows), "Checking your installed skills") {
		t.Errorf("the long command pushed the earlier action out of a region that had room:\n%s", strings.Join(rows, "\n"))
	}
}

// The repeat counter is what turns twenty flickering lines into "this is
// happening over and over" — and the calls that repeat are the long ones.
func TestRepeatCounterSurvivesWrapping(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	for i := 0; i < 13; i++ {
		m = feed(t, m, shellCall(longShell))
	}
	got := flat(rowsOf(m.renderLiveRegion()))
	if !strings.Contains(got, "x13") {
		t.Errorf("the repeat count was lost in the wrap: %s", got)
	}
	// Both, or the counter is being bought with the command it counts —
	// which is what the old renderer did, reserving the suffix by cutting
	// the text.
	if !strings.Contains(despace(got), despace(longShell)) {
		t.Errorf("the counter survived but the command it counts did not: %s", got)
	}
}

// Wrapping mid-stream must not yank the pane. The region growing is fine — it
// happens at the bottom, where the eye already is. Shrinking is not: it drops
// the transcript above back down mid-sentence.
func TestWorkLogHeightNeverShrinksMidTurn(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	peak := 0
	for i := 0; i < 10; i++ {
		m = feed(t, m,
			shellCall(longShell+strings.Repeat(" --x", i)),
			event.CanonicalToolResultEvent{
				Type: "tool_result", Tool: "run_shell_command",
				Preview: "failed - the remote closed the connection after 30s; check the network and retry",
			},
		)
		n := len(rowsOf(m.liveRegionView()))
		if n < peak {
			t.Fatalf("the work log shrank from %d rows to %d mid-turn — the transcript above it just jumped", peak, n)
		}
		if budget := m.logRows(); n > budget {
			t.Fatalf("holding the height pushed the region to %d rows, past its %d-row budget", n, budget)
		}
		peak = n
	}

	// A new turn starts from nothing: carrying the last turn's height over
	// would open with a block of blank rows.
	next, _ := m.startTurn("what changed?")
	fresh := next.(ChatModel)
	if got := len(rowsOf(fresh.liveRegionView())); got > 2 {
		t.Errorf("a new turn opened %d rows tall; the previous turn's height leaked into it", got)
	}
}

// The wrap itself, at the sizes and shapes its callers hand it.
func TestWrapLog(t *testing.T) {
	cases := []struct {
		name          string
		text, suffix  string
		width, maxRow int
		wantRows      int
		wantContains  []string
		wantCut       bool
	}{
		{
			name: "short text is one row, untouched",
			text: "Checking your installed skills", width: 74, maxRow: 3,
			wantRows: 1, wantContains: []string{"Checking your installed skills"},
		},
		{
			name: "long text wraps instead of losing its tail",
			text: longShell, width: 74, maxRow: 3,
			wantRows: 2, wantContains: []string{"gh issue list", "length'"},
		},
		{
			name: "past the row cap it is cut, and says so",
			text: strings.Repeat("word ", 200), width: 40, maxRow: 2,
			wantRows: 2, wantCut: true,
		},
		{
			name: "the repeat counter survives the cut",
			text: strings.Repeat("word ", 200), suffix: " x14", width: 40, maxRow: 2,
			wantRows: 2, wantContains: []string{"x14"}, wantCut: true,
		},
		{
			// The cut row lands EXACTLY on the measure, which is the common
			// case (WrapText fills rows to the limit), not a boundary one.
			name: "an exactly-full cut row still says it was cut",
			text: strings.Repeat("word ", 200), width: 39, maxRow: 2,
			wantRows: 2, wantCut: true,
		},
		{
			// WrapText breaks on spaces and this has none, so without a hard
			// break the row runs past the pane and the viewport eats the tail.
			name: "one unbroken token is broken by force",
			text: longURL, width: 40, maxRow: 4,
			wantRows: 4, wantContains: []string{"https://github.com"},
		},
		{
			name: "a nonsense width still yields a row",
			text: "something happened", width: 0, maxRow: 0,
			wantRows: 1,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rows := wrapLog(tc.text, tc.suffix, tc.width, tc.maxRow)
			if len(rows) != tc.wantRows {
				t.Errorf("got %d rows, want %d: %q", len(rows), tc.wantRows, rows)
			}
			joined := strings.Join(rows, " ")
			for _, want := range tc.wantContains {
				if !strings.Contains(joined, want) {
					t.Errorf("wrapped text lost %q: %q", want, joined)
				}
			}
			if tc.wantCut {
				last := strings.TrimSpace(rows[len(rows)-1])
				if !strings.HasSuffix(last, strings.TrimSpace(tc.suffix)) {
					t.Errorf("the repeat counter is not at the end of the cut row: %q", last)
				}
				if !strings.Contains(joined, "…") {
					t.Errorf("a cut with nothing on screen to say it was cut: %q", joined)
				}
			}
			for _, r := range rows {
				if w := ansi.StringWidth(r); tc.width > 0 && w > tc.width {
					t.Errorf("row is %d columns, measure is %d: %q", w, tc.width, r)
				}
			}
		})
	}
}

// The cut marker has to land in the TEXT. Appended to the rendered row instead,
// it follows the live line's elapsed clock — where it reads as part of the timer
// rather than as "there is more of this command than fits".
func TestTheCutMarkerLandsInTheTextNotAfterTheClock(t *testing.T) {
	m := chatAt(t, term{"tiny", 60, 12})
	// 12 rows leaves the region two, and the still-working hint (nothing has
	// completed after 20s) takes one of them — so the live row is the ONLY row,
	// and it is the row carrying the clock.
	m.queryStart = time.Now().Add(-30 * time.Second)
	m = feed(t, m, shellCall(strings.Repeat("gaia --flag ", 40)))
	rows := rowsOf(m.renderLiveRegion())
	t.Logf("\n%s", strings.Join(rows, "\n"))

	if !strings.Contains(flat(rows), "…") {
		t.Fatalf("the cut was not marked at all:\n%s", strings.Join(rows, "\n"))
	}
	for _, r := range rows {
		if liveClock.MatchString(strings.TrimRight(r, "… ")) && strings.HasSuffix(strings.TrimRight(r, " "), "…") {
			t.Errorf("the cut marker was appended after the elapsed clock: %q", r)
		}
	}
}

// Only the first row of a live action carries the clock, so only the first row
// pays for it. Charging every wrapped row for a number none of them shows threw
// away two words a row on the longest lines in the log.
func TestOnlyTheClockRowPaysForTheClock(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	m = feed(t, m, shellCall(longShell+" "+longShell))
	rows := rowsOf(m.renderLiveRegion())
	t.Logf("\n%s", strings.Join(rows, "\n"))

	widest := 0
	for _, r := range rows[1:] {
		if w := ansi.StringWidth(strings.TrimSpace(r)); w > widest {
			widest = w
		}
	}
	if narrowed := m.logWidth() - elapsedReserve; widest <= narrowed {
		t.Errorf("continuation rows reach only %d columns; the measure without the clock is %d", widest, m.logWidth())
	}
	for _, r := range rows {
		if w := ansi.StringWidth(r); w > 80 {
			t.Errorf("row is %d columns on an 80-column terminal: %q", w, r)
		}
	}
}

// Several phrases put words AFTER the argument. An argument allowed to fill the
// whole line pushes them off the end, leaving the reader a query with no clue
// what was done with it.
func TestALongArgumentCannotEatItsOwnPhrase(t *testing.T) {
	args, _ := json.Marshal(map[string]string{"query": strings.Repeat("retrieval augmented generation ", 12)})
	got := toolNarration("search_indexed_chunks", args, "")
	t.Logf("%q", got)

	if !strings.HasPrefix(got, "Looking for ") {
		t.Errorf("the phrase lost its opening: %q", got)
	}
	if !strings.Contains(got, "in your documents") {
		t.Errorf("the argument ate the clause that says what was done with it: %q", got)
	}
}

// The held height pads ABOVE the log. Below, the blank rows open a gap between
// the log and the answer streaming under it.
func TestHeldHeightPadsAboveTheLog(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	for i := 0; i < 6; i++ {
		m = feed(t, m,
			shellCall(longShell+strings.Repeat(" --x", i)),
			event.CanonicalToolResultEvent{
				Type: "tool_result", Tool: "run_shell_command",
				Preview: "failed - the remote closed the connection after 30s; check the network and retry",
			},
		)
	}
	m.activity = nil // the log empties, the held height stays
	rows := rowsOf(m.liveRegionView())
	t.Logf("%d rows:\n%s", len(rows), strings.Join(rows, "\n"))

	if last := strings.TrimSpace(rows[len(rows)-1]); last == "" {
		t.Errorf("the region ends in blank rows — the pad is under the log, not above it:\n%q", rows)
	}
}
