// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/event"
)

// delegateAdvice is the model-facing text no view may print.
const delegateAdvice = "A FAILED helper returned nothing usable: do that work yourself or tell the user it is missing."

func delegateCall(narration string) event.CanonicalToolCallEvent {
	return event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "delegate_task", Narration: narration,
		Args: json.RawMessage(`{"tasks":[{"task":"a"},{"task":"b"},{"task":"c"},{"task":"d"}]}`),
	}
}

func helpersStatus(message string) event.CanonicalStatusEvent {
	return event.CanonicalStatusEvent{Type: "status", Message: message, Phase: "helpers"}
}

type helperFixture struct {
	ID         int    `json:"id"`
	Task       string `json:"task"`
	Status     string `json:"status"`
	Reason     string `json:"reason,omitempty"`
	Conclusion string `json:"conclusion,omitempty"`
	Error      string `json:"error,omitempty"`
}

// delegateResult builds the tool_result the agent sends for a delegation.
func delegateResult(t *testing.T, status, summary string, helpers []helperFixture) event.CanonicalToolResultEvent {
	t.Helper()
	body := map[string]interface{}{"status": status, "summary": summary, "helpers": helpers, "width": 4}
	preview := summary
	if status != "success" {
		body["advice"] = delegateAdvice
	}
	if status == "error" {
		body["error"] = summary + " " + delegateAdvice
		// What an agent older than the preview fix sends.
		preview = summary + " " + delegateAdvice
	}
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return event.CanonicalToolResultEvent{Type: "tool_result", Tool: "delegate_task", Data: data, Preview: preview}
}

var mixedHelpers = []helperFixture{
	{ID: 1, Task: "Who owns notes_01.md and when is it due?", Status: "ok", Conclusion: "Owner: Ada Okonkwo; due 14 March\nEvidence: notes_01.md line 3"},
	{ID: 2, Task: "Who owns notes_02.md and when is it due?", Status: "ok", Conclusion: "Owner: Li Wei; due 3 April"},
	{ID: 3, Task: "Summarise the Q3 pricing change", Status: "partial", Reason: "step_limit", Conclusion: "Pricing rises 4% in the third quarter for every plan"},
	{ID: 4, Task: "Find the budget in notes_04.md", Status: "failed", Reason: "timeout", Error: "No answer within 600 s."},
}

// modelAt is a streaming chat at one width, folded or expanded.
func modelAt(t *testing.T, width int, expanded bool) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, "gaia", "")
	m.width, m.height = width, 60
	m.resize()
	m.streaming = true
	m.queryStart = time.Now()
	m.expandWork = expanded
	return m
}

func liveRows(m ChatModel) []string { return rowsOf(m.renderLiveRegion()) }

func statusItems(m ChatModel) int {
	n := 0
	for _, item := range m.activity {
		if item.Kind == "status" {
			n++
		}
	}
	return n
}

func noRowWider(t *testing.T, rows []string, width int) {
	t.Helper()
	for _, r := range rows {
		if w := displayWidth(r); w > width {
			t.Errorf("row is %d columns on a %d-column window: %q", w, width, r)
		}
	}
}

// Progress rides on the status line while the helpers run, in every view, and
// adds no row to the work log.
func TestHelperProgressRidesOnTheStatusLine(t *testing.T) {
	narrations := []string{"Handing 4 tasks to helpers", "Handing work to helpers"}
	steps := []struct{ message, suffix string }{
		{"", ""},
		{"1 of 4 helpers done", " · 1 of 4 done"},
		{"2 of 4 helpers done", " · 2 of 4 done"},
		{"3 of 4 helpers done, 1 failed", " · 3 of 4 done, 1 failed"},
	}
	for _, expanded := range []bool{false, true} {
		for _, narration := range narrations {
			t.Run(fmt.Sprint(expanded)+"/"+narration, func(t *testing.T) {
				m := feed(t, modelAt(t, 100, expanded), delegateCall(narration))
				for _, s := range steps {
					if s.message != "" {
						m = feed(t, m, helpersStatus(s.message))
					}
					if n := statusItems(m); n != 0 {
						t.Fatalf("after %q the log holds %d status rows: %+v", s.message, n, m.activity)
					}
					if m.phase.name != "" {
						t.Fatalf("after %q the helpers phase replaced the step: %+v", s.message, m.phase)
					}
					line := ansi.Strip(m.statusLine(m.now()))
					if !strings.Contains(line, narration+s.suffix+"…") {
						t.Errorf("after %q the status line reads %q, want %q", s.message, line, narration+s.suffix)
					}
					if live := strings.Join(liveRows(m), "\n"); strings.Contains(live, "helpers done") || strings.Contains(live, narration) {
						t.Errorf("the running delegation is in the work log as well: %q", live)
					}
				}
			})
		}
	}
}

func TestOneHelperProgressDropsTheWordToo(t *testing.T) {
	if got := helperProgress("1 of 1 helper done"); got != "1 of 1 done" {
		t.Errorf("got %q", got)
	}
	if got := helperProgress("16 of 16 helpers done, 12 failed"); got != "16 of 16 done, 12 failed" {
		t.Errorf("got %q", got)
	}
}

// Any other phase still drives the status line and nothing else.
func TestOtherPhasesStillDriveTheStatusLine(t *testing.T) {
	m := feed(t, modelAt(t, 100, false),
		event.CanonicalStatusEvent{Type: "status", Message: "Reasoning", Phase: "reasoning", Words: 1200})
	if len(m.activity) != 0 {
		t.Fatalf("a phase report landed in the work log: %+v", m.activity)
	}
	if m.phase.name != "reasoning" || m.phase.words != 1200 {
		t.Fatalf("phase not recorded: %+v", m.phase)
	}
	if line := ansi.Strip(m.statusLine(m.now())); !strings.Contains(line, "Reasoning · 1,200 words") {
		t.Errorf("status line: %q", line)
	}

	// With a tool open, another phase is still not put on its step.
	m = feed(t, m, delegateCall("Handing 4 tasks to helpers"),
		event.CanonicalStatusEvent{Type: "status", Message: "Loading more tools", Phase: "loading_tools"})
	if m.activity[0].Progress != "" || m.phase.name != "loading_tools" {
		t.Errorf("another phase was treated as helper progress: %+v, %+v", m.activity[0], m.phase)
	}
}

// With no step to carry it, helper progress is an ordinary status.
func TestHelperProgressWithNoOpenToolIsANormalStatus(t *testing.T) {
	for _, tc := range []struct {
		name string
		prep []interface{}
	}{
		{"nothing running", nil},
		{"the tool already finished", []interface{}{
			event.CanonicalToolCallEvent{Type: "tool_call", Tool: "read_file"},
			event.CanonicalToolResultEvent{Type: "tool_result", Tool: "read_file", Data: json.RawMessage(`{"ok":true}`)},
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := feed(t, modelAt(t, 100, true), tc.prep...)
			m = feed(t, m, helpersStatus("2 of 4 helpers done"))
			last := m.activity[len(m.activity)-1]
			if last.Kind != "status" || last.Content != "2 of 4 helpers done" {
				t.Fatalf("the status was not kept as a normal one: %+v", m.activity)
			}
			if m.phase.name != "" {
				t.Errorf("it also became a phase: %+v", m.phase)
			}
			if rows := liveRows(m); !strings.Contains(strings.Join(rows, "\n"), "2 of 4 helpers done") {
				t.Errorf("not on screen when expanded: %q", rows)
			}
		})
	}
}

// Success, partial and error each read differently, in words and in colour.
func TestFinishedDelegationRow(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })

	allOK := []helperFixture{{ID: 1, Task: "a", Status: "ok", Conclusion: "x"}, {ID: 2, Task: "b", Status: "ok", Conclusion: "y"}}
	allFailed := []helperFixture{{ID: 1, Task: "a", Status: "failed", Reason: "error"}, {ID: 2, Task: "b", Status: "failed", Reason: "error"}}
	cases := []struct {
		name, status, summary string
		helpers               []helperFixture
		wantDetail            string
		wantFailed, wantWarn  bool
		wantHint              bool
	}{
		{"success", "success", "2 of 2 helpers finished.", allOK, "└ 2 of 2 helpers finished.", false, false, false},
		{"partial", "partial", "2 of 4 helpers finished, 1 stopped short, 1 failed.", mixedHelpers,
			"└ 2 of 4 helpers finished, 1 stopped short, 1 failed.", false, true, true},
		{"error", "error", "0 of 2 helpers finished, 2 failed.", allFailed,
			"└ ✗ failed: 0 of 2 helpers finished, 2 failed.", true, false, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := feed(t, modelAt(t, 100, false), delegateCall("Handing work to helpers"),
				helpersStatus("1 of 2 helpers done"), delegateResult(t, tc.status, tc.summary, tc.helpers))
			item := m.activity[0]
			if !item.Done || item.Progress != "" {
				t.Fatalf("the step was not closed cleanly: %+v", item)
			}
			if failed := item.Success != nil && !*item.Success; failed != tc.wantFailed {
				t.Errorf("failed = %v, want %v", failed, tc.wantFailed)
			}
			if item.Partial != tc.wantWarn {
				t.Errorf("Partial = %v, want %v", item.Partial, tc.wantWarn)
			}
			for _, msg := range m.messages {
				if msg.Role == RoleError {
					t.Errorf("a delegation drew an error panel: %q", msg.Content)
				}
			}

			// The settled record, as the transcript keeps it after the turn.
			work := Message{Role: RoleWork, Work: m.activity}
			styled := m.renderWork(&work)
			plain := ansi.Strip(styled)
			if strings.Contains(plain, "yourself") || strings.Contains(plain, "FAILED helper") {
				t.Errorf("the model's advice is on screen:\n%s", plain)
			}
			rows := strings.Split(plain, "\n")
			if len(rows) != 2 {
				t.Fatalf("folded, a delegation is its row and one detail row; got %q", rows)
			}
			detail := strings.TrimSpace(rows[1])
			wantRow := tc.wantDetail
			if tc.wantHint {
				wantRow += " · Ctrl+O details"
			}
			if detail != wantRow {
				t.Errorf("detail row %q, want %q", detail, wantRow)
			}
			for style, want := range map[string]bool{
				failStyle.Render(tc.wantDetail):       tc.wantFailed,
				helperWarnStyle.Render(tc.wantDetail): tc.wantWarn,
				detailStyle.Render(tc.wantDetail):     !tc.wantFailed && !tc.wantWarn,
			} {
				if strings.Contains(styled, style) != want {
					t.Errorf("detail colour wrong (want %v for %q) in:\n%q", want, style, styled)
				}
			}
			if red := strings.Contains(styled, dotFailStyle.Render(glyphDot)); red != tc.wantFailed {
				t.Errorf("bullet marked failed = %v, want %v", red, tc.wantFailed)
			}
		})
	}
}

// While the turn runs, a delegation with nothing usable is an error the agent
// may still work past, in the same words every failed step uses.
func TestAllFailedDelegationWhileTheTurnRuns(t *testing.T) {
	m := feed(t, modelAt(t, 100, false), delegateCall("Handing 2 tasks to helpers"),
		delegateResult(t, "error", "0 of 2 helpers finished, 2 failed.", []helperFixture{
			{ID: 1, Task: "a", Status: "failed", Reason: "error"}, {ID: 2, Task: "b", Status: "failed", Reason: "error"}}))
	plain := strings.Join(liveRows(m), "\n")
	if !strings.Contains(plain, "└ error: 0 of 2 helpers finished, 2 failed.") {
		t.Errorf("live detail:\n%s", plain)
	}
	if strings.Contains(plain, "yourself") {
		t.Errorf("the model's advice is on screen:\n%s", plain)
	}
}

func expandedRows(t *testing.T, width int, res event.CanonicalToolResultEvent) (ChatModel, []string) {
	t.Helper()
	m := feed(t, modelAt(t, width, true), delegateCall("Handing 4 tasks to helpers"), res)
	work := Message{Role: RoleWork, Work: m.activity}
	return m, strings.Split(ansi.Strip(m.renderWork(&work)), "\n")
}

// Ctrl+O lists every helper: mark, number, task, then what it concluded.
func TestExpandedDelegationListsEachHelper(t *testing.T) {
	_, rows := expandedRows(t, 120, delegateResult(t, "partial",
		"2 of 4 helpers finished, 1 stopped short, 1 failed.", mixedHelpers))
	want := []string{
		"  ● Handing 4 tasks to helpers",
		"      2 of 4 helpers finished, 1 stopped short, 1 failed.",
		"       ✓ 1  Who owns notes_01.md and when is it due?   Owner: Ada Okonkwo; due 14 March",
		"       ✓ 2  Who owns notes_02.md and when is it due?   Owner: Li Wei; due 3 April",
		"       ~ 3  Summarise the Q3 pricing change            stopped short (out of steps): Pricing rises 4% in the third qu…",
		"       ✗ 4  Find the budget in notes_04.md             failed: no answer in time",
	}
	if len(rows) != len(want) {
		t.Fatalf("got %d rows, want %d:\n%s", len(rows), len(want), strings.Join(rows, "\n"))
	}
	for i := range want {
		if rows[i] != want[i] {
			t.Errorf("row %d\n got %q\nwant %q", i, rows[i], want[i])
		}
	}
	noRowWider(t, rows, 120)
}

// Folded shows no helper rows; the hint says where they are.
func TestFoldedDelegationShowsNoHelperRows(t *testing.T) {
	m := feed(t, modelAt(t, 120, false), delegateCall("Handing 4 tasks to helpers"),
		delegateResult(t, "partial", "2 of 4 helpers finished, 1 stopped short, 1 failed.", mixedHelpers))
	work := Message{Role: RoleWork, Work: m.activity}
	plain := ansi.Strip(m.renderWork(&work))
	if strings.ContainsAny(plain, "✓✗~") || strings.Contains(plain, "notes_01") {
		t.Errorf("helper rows shown while folded:\n%s", plain)
	}
	if !strings.Contains(plain, "Ctrl+O details") {
		t.Errorf("no hint at full width:\n%s", plain)
	}
	// Too narrow for the whole hint: it is left out, not clipped.
	m.width = 62
	plain = ansi.Strip(m.renderWork(&work))
	if strings.Contains(plain, "Ctrl") {
		t.Errorf("a clipped hint on a narrow window:\n%s", plain)
	}
	noRowWider(t, strings.Split(plain, "\n"), 62)
}

func TestHelperReasonsInPlainWords(t *testing.T) {
	reasons := map[string]string{
		"step_limit": "out of steps",
		"loop":       "kept repeating a step",
		"empty":      "no answer",
		"error":      "the model server failed",
		"timeout":    "no answer in time",
		"cancelled":  "stopped",
		"new_reason": "new reason",
	}
	for reason, words := range reasons {
		failed := helperOutcomeText(helperOutcome{Status: helperFailed, Reason: reason, Conclusion: "ignored"})
		if failed != "failed: "+words {
			t.Errorf("failed/%s: %q", reason, failed)
		}
		short := helperOutcomeText(helperOutcome{Status: helperPartial, Reason: reason, Conclusion: "Half an answer"})
		if short != "stopped short ("+words+"): Half an answer" {
			t.Errorf("partial/%s: %q", reason, short)
		}
	}
	if got := helperOutcomeText(helperOutcome{Status: helperFailed}); got != "failed" {
		t.Errorf("no reason: %q", got)
	}
	if got := helperOutcomeText(helperOutcome{Status: helperPartial, Conclusion: "x"}); got != "stopped short: x" {
		t.Errorf("partial, no reason: %q", got)
	}
	if got := helperOutcomeText(helperOutcome{Status: helperOK, Conclusion: "Owner: Ada"}); got != "Owner: Ada" {
		t.Errorf("ok: %q", got)
	}
}

// A status this client has never heard of is not drawn as a success.
func TestUnknownHelperStatusReadsAsFailed(t *testing.T) {
	d, ok := delegationOf(json.RawMessage(
		`{"status":"partial","summary":"1 of 2 helpers finished.","helpers":[{"id":2,"task":"b","status":"exploded"},{"id":1,"task":"a","status":"ok","conclusion":"x"}]}`))
	if !ok || len(d.helpers) != 2 {
		t.Fatalf("not read: %+v", d)
	}
	if d.helpers[0].ID != 1 || d.helpers[1].ID != 2 {
		t.Errorf("not in id order: %+v", d.helpers)
	}
	if d.helpers[1].Status != helperFailed {
		t.Errorf("unknown status drawn as %q", d.helpers[1].Status)
	}
}

// One row per helper at any width: clipped with an ellipsis, never wrapped.
func TestHelperRowsClipAtAnyWidth(t *testing.T) {
	nasty := []helperFixture{
		{ID: 1, Task: "Line one\nline two\twith a tab \x1b[31mand red\x1b[0m \x07bell " + strings.Repeat("W", 300),
			Status: "ok", Conclusion: "\n\n  First real line \x1b[1mbold\x1b[0m\tend\nsecond line"},
		{ID: 2, Task: "short", Status: "partial", Reason: "loop", Conclusion: strings.Repeat("long conclusion ", 40)},
		{ID: 3, Task: strings.Repeat("词", 200), Status: "failed", Reason: "empty"},
	}
	for _, width := range []int{60, 100, 160} {
		t.Run(fmt.Sprint(width), func(t *testing.T) {
			m, rows := expandedRows(t, width, delegateResult(t, "partial", "1 of 3 helpers finished, 1 stopped short, 1 failed.", nasty))
			// The step, its summary (wrapped on a narrow window), then one row per helper.
			if len(rows) < 5 {
				t.Fatalf("want the step, its summary and 3 helper rows, got %d:\n%s", len(rows), strings.Join(rows, "\n"))
			}
			helperRows := rows[len(rows)-3:]
			for i, mark := range []string{"✓ 1", "~ 2", "✗ 3"} {
				if !strings.HasPrefix(strings.TrimSpace(helperRows[i]), mark) {
					t.Fatalf("helper row %d is not one row: %q\n%s", i+1, helperRows[i], strings.Join(rows, "\n"))
				}
			}
			noRowWider(t, rows, width)
			all := strings.Join(rows, "\n")
			for _, bad := range []string{"\x1b", "\x07", "\t"} {
				if strings.Contains(all, bad) {
					t.Errorf("control text %q reached the screen", bad)
				}
			}
			if flatTask := "Line one line two with a tab and red bell W"; !strings.Contains(helperRows[0], flatTask[:13]) ||
				(width == 160 && !strings.Contains(helperRows[0], flatTask)) {
				t.Errorf("task not flattened to one line: %q", helperRows[0])
			}
			if !strings.Contains(helperRows[0], "…") {
				t.Errorf("a clipped task shows no ellipsis: %q", helperRows[0])
			}
			if !strings.Contains(helperRows[0], "First real") || strings.Contains(helperRows[0], "second line") {
				t.Errorf("conclusion is not its first line: %q", helperRows[0])
			}
			if !strings.Contains(helperRows[1], "stopped short (kept") || !strings.HasSuffix(helperRows[1], "…") {
				t.Errorf("partial row: %q", helperRows[1])
			}
			if !strings.HasSuffix(helperRows[2], "failed: no answer") {
				t.Errorf("failed row: %q", helperRows[2])
			}
			// Outcomes start in one column.
			col := -1
			for i, marker := range []string{"First real", "stopped short", "failed: no answer"} {
				at := displayWidth(helperRows[i][:strings.Index(helperRows[i], marker)])
				if col >= 0 && at != col {
					t.Errorf("outcome column %d on helper row %d, %d above it:\n%s", at, i+1, col, all)
				}
				col = at
			}
			// The live region holds the same rows to its own height budget.
			if live := liveRows(m); len(live) > m.logRows() {
				t.Errorf("live region is %d rows, budget %d", len(live), m.logRows())
			}
		})
	}
}

func TestSixteenHelpersAreAllListed(t *testing.T) {
	var helpers []helperFixture
	for i := 16; i >= 1; i-- {
		helpers = append(helpers, helperFixture{ID: i, Task: fmt.Sprintf("Read notes_%02d.md and report its owner", i),
			Status: "ok", Conclusion: fmt.Sprintf("Owner %d", i)})
	}
	m, rows := expandedRows(t, 100, delegateResult(t, "success", "16 of 16 helpers finished.", helpers))
	if len(rows) != 18 {
		t.Fatalf("want 18 rows, got %d:\n%s", len(rows), strings.Join(rows, "\n"))
	}
	for i := 1; i <= 16; i++ {
		want := fmt.Sprintf("       ✓ %2d  Read notes_%02d.md and report its owner   Owner %d", i, i, i)
		if rows[i+1] != want {
			t.Errorf("row %d\n got %q\nwant %q", i, rows[i+1], want)
		}
	}
	noRowWider(t, rows, 100)

	// Mid-turn the list is cut to the live region's height, step row first.
	live := liveRows(m)
	if len(live) > m.logRows() {
		t.Fatalf("live region is %d rows, budget %d:\n%s", len(live), m.logRows(), strings.Join(live, "\n"))
	}
	if joined := strings.Join(live, "\n"); !strings.Contains(joined, "Handing 4 tasks to helpers") {
		t.Errorf("the cut list lost its step row:\n%s", joined)
	}
}

// More helpers than the agent's own limit are counted, not dropped unsaid.
func TestHelpersPastTheLimitAreCounted(t *testing.T) {
	var helpers []helperFixture
	for i := 1; i <= 20; i++ {
		helpers = append(helpers, helperFixture{ID: i, Task: "t", Status: "ok", Conclusion: "c"})
	}
	_, rows := expandedRows(t, 100, delegateResult(t, "success", "20 of 20 helpers finished.", helpers))
	if len(rows) != 19 || strings.TrimSpace(rows[18]) != "… 4 more" {
		t.Errorf("got %d rows, last %q", len(rows), rows[len(rows)-1])
	}
}

// A result cut down on the wire has no helper list: only the detail row.
func TestTruncatedDelegationShowsOnlyTheDetailRow(t *testing.T) {
	for _, tc := range []struct{ name, data string }{
		{"truncated", `{"truncated":true,"text":"{\"advice\":\"A FAILED helper returned nothing usable: do that work yourself\",\"helpers\":[{\"id\":1"}`},
		{"no helpers", `{"status":"success","summary":"2 of 2 helpers finished."}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			res := event.CanonicalToolResultEvent{Type: "tool_result", Tool: "delegate_task",
				Data: json.RawMessage(tc.data), Preview: "2 of 2 helpers finished."}
			m, rows := expandedRows(t, 100, res)
			if len(m.activity[0].Helpers) != 0 {
				t.Fatalf("helpers read out of a result with no list: %+v", m.activity[0].Helpers)
			}
			if len(rows) != 2 || strings.TrimSpace(rows[1]) != "2 of 2 helpers finished." {
				t.Errorf("rows: %q", rows)
			}
			if strings.Contains(strings.Join(rows, "\n"), "yourself") {
				t.Errorf("advice on screen: %q", rows)
			}
		})
	}
}

// Two delegations in a row keep a bullet each: joined, one helper list would
// hide the other's.
func TestConsecutiveDelegationsDoNotFold(t *testing.T) {
	ok := []helperFixture{{ID: 1, Task: "a", Status: "ok", Conclusion: "x"}}
	m := feed(t, modelAt(t, 100, true),
		delegateCall("Handing 1 task to helpers"), delegateResult(t, "success", "1 of 1 helper finished.", ok),
		delegateCall("Handing 1 task to helpers"), delegateResult(t, "success", "1 of 1 helper finished.", ok),
		delegateCall("Handing 1 task to helpers"), helpersStatus("1 of 1 helper done"))
	steps := 0
	for _, r := range liveRows(m) {
		if strings.Contains(r, "Handing 1 task to helpers") {
			steps++
		}
	}
	if steps != 2 {
		t.Errorf("want two finished bullets, got %d:\n%s", steps, strings.Join(liveRows(m), "\n"))
	}
	if line := ansi.Strip(m.statusLine(m.now())); !strings.Contains(line, "Handing 1 task to helpers · 1 of 1 done") {
		t.Errorf("the running one lost its progress: %q", line)
	}
}
