package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/event"
)

// A step error the agent retries past is part of a normal turn: it keeps its
// one-line error, in amber, and only a failure the turn ENDS on is drawn red.

const scriptError = `{"status":"error","error":"error: Python exited with code 1: ValueError: Length mismatch: Expected axis has 7 elements"}`

func call(tool, args string) event.CanonicalToolCallEvent {
	return event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool, Args: json.RawMessage(args)}
}

func result(tool, data string) event.CanonicalToolResultEvent {
	return event.CanonicalToolResultEvent{Type: "tool_result", Tool: tool, Data: json.RawMessage(data)}
}

var turnEnds = event.CanonicalFinalEvent{Type: "final", Answer: "Done."}

// colour turns ANSI colour on for one test and returns failStyle's opening
// sequence, so a test can ask whether anything on screen was drawn red.
func colour(t *testing.T) string {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
	red, _, ok := strings.Cut(failStyle.Render("x"), "x")
	if !ok || red == "" {
		t.Fatal("failStyle drew no colour under TrueColor")
	}
	return red
}

// workRecord is the finished turn's work log exactly as drawn, colour included.
func workRecord(t *testing.T, m ChatModel) string {
	t.Helper()
	for i := range m.messages {
		if m.messages[i].Role == RoleWork {
			return m.renderWork(&m.messages[i])
		}
	}
	t.Fatalf("the turn left no work record: %+v", m.messages)
	return ""
}

func TestARetriedScriptErrorEndingInSuccessIsNotRed(t *testing.T) {
	red := colour(t)
	m := feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", scriptError),
		call("write_file", `{"file_path":"clean.py"}`),
		result("write_file", `{"success":true}`),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", `{"success":true,"summary":"wrote 8 rows"}`),
	)

	live := m.renderLiveRegion()
	if strings.Contains(live, red) {
		t.Errorf("a retried error was drawn red mid-turn:\n%s", ansi.Strip(live))
	}
	if got := ansi.Strip(live); !strings.Contains(got, "error, retried") ||
		!strings.Contains(got, "Length mismatch") {
		t.Errorf("the retried step must keep its one-line error:\n%s", got)
	}

	m = feed(t, m, turnEnds)
	record := workRecord(t, m)
	if strings.Contains(record, red) {
		t.Errorf("a recovered turn's record has red in it:\n%s", ansi.Strip(record))
	}
	got := ansi.Strip(record)
	if !strings.Contains(got, "└ error, retried: Python exited with code 1: ValueError") {
		t.Errorf("the record lost the retried error:\n%s", got)
	}
	if strings.Contains(got, "failed") {
		t.Errorf("a recovered error must not read as failed:\n%s", got)
	}
}

func TestAScriptErrorWithNoRetryBeforeTheTurnEndsIsRed(t *testing.T) {
	red := colour(t)
	m := feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", scriptError),
	)

	// Not known yet: the agent may retry, so no alarm and no claim it will.
	live := m.renderLiveRegion()
	if strings.Contains(live, red) {
		t.Errorf("a provisional error was drawn red:\n%s", ansi.Strip(live))
	}
	if got := ansi.Strip(live); !strings.Contains(got, "└ error: Python exited") ||
		strings.Contains(got, "retried") {
		t.Errorf("a provisional error must say error, and nothing about a retry:\n%s", got)
	}

	m = feed(t, m, turnEnds)
	record := workRecord(t, m)
	if !strings.Contains(record, red) {
		t.Errorf("the failure the turn ended on must be red:\n%s", ansi.Strip(record))
	}
	if got := ansi.Strip(record); !strings.Contains(got, "└ failed") ||
		!strings.Contains(got, "Length mismatch") {
		t.Errorf("the failure the turn ended on must say failed, with its error:\n%s", got)
	}
}

func TestAnErrorFollowedByADifferentToolsSuccessStaysRed(t *testing.T) {
	red := colour(t)
	m := feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", scriptError),
		call("list_files", `{"path":"."}`),
		result("list_files", `{"success":true,"files":["a","b"]}`),
		turnEnds,
	)

	got := ansi.Strip(workRecord(t, m))
	if strings.Contains(got, "retried") || !strings.Contains(got, "└ failed") {
		t.Errorf("another tool's success does not recover this one:\n%s", got)
	}
	if !strings.Contains(workRecord(t, m), red) {
		t.Errorf("the unrecovered failure must be red")
	}
}

// A shell command is recovered only by the same program doing the same kind of
// thing: a later `python -c` passing says nothing about a `pip install`.
func TestAShellFailureIsRecoveredOnlyByTheSameKindOfCommand(t *testing.T) {
	pipFailed := `{"status":"error","error":"Command failed (exit 1): python.exe: No module named pip"}`
	m := feed(t, newTestChat(t),
		call("run_shell_command", `{"command":"python -m pip install formulas"}`),
		result("run_shell_command", pipFailed),
		call("run_shell_command", `{"command":"python -c \"import openpyxl\""}`),
		result("run_shell_command", `{"status":"success","return_code":0}`),
		turnEnds,
	)
	if got := ansi.Strip(workRecord(t, m)); !strings.Contains(got, "└ failed") ||
		strings.Contains(got, "retried") {
		t.Errorf("a different command's success recovered the pip failure:\n%s", got)
	}

	m = feed(t, newTestChat(t),
		call("run_shell_command", `{"command":"python -m pip install formulas"}`),
		result("run_shell_command", pipFailed),
		call("run_shell_command", `{"command":"python -m pip install --user formulas"}`),
		result("run_shell_command", `{"status":"success","return_code":0}`),
		turnEnds,
	)
	// Back to back, a settled attempt folds into its retry as before ("x2").
	if got := ansi.Strip(workRecord(t, m)); strings.Contains(got, "failed") ||
		!strings.Contains(got, "x2") {
		t.Errorf("the same command retried and passing must settle it:\n%s", got)
	}
}

// Folding a standing failure under the next call of the same tool would put
// that call's outcome in its place, and the failure would vanish.
func TestAStandingFailureIsNeverFoldedAway(t *testing.T) {
	m := feed(t, newTestChat(t),
		call("run_shell_command", `{"command":"python -m pip install formulas"}`),
		result("run_shell_command", `{"status":"error","error":"Command failed (exit 1): No module named pip"}`),
		call("run_shell_command", `{"command":"dir"}`),
		result("run_shell_command", `{"status":"success","return_code":0}`),
	)
	if got := ansi.Strip(m.renderLiveRegion()); !strings.Contains(got, "No module named pip") {
		t.Errorf("the pip failure was folded under the next command:\n%s", got)
	}
}

// Only a LATER success settles a failure: one that came before it is history.
func TestASuccessBeforeTheFailureDoesNotRecoverIt(t *testing.T) {
	m := feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", `{"success":true}`),
		call("write_file", `{"file_path":"clean.py"}`),
		result("write_file", `{"success":true}`),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", scriptError),
		turnEnds,
	)
	if got := ansi.Strip(workRecord(t, m)); !strings.Contains(got, "└ failed") {
		t.Errorf("an earlier success recovered a later failure:\n%s", got)
	}
}

func TestSettleFailuresLeavesTheStoredLogUntouched(t *testing.T) {
	no := false
	items := []ActivityItem{{Kind: "tool", Tool: "run_python", Done: true, Success: &no, Detail: "failed: boom"}}
	settled := settleFailures(items, false)
	if settled[0].Recovery != recoveryPending || items[0].Recovery != "" {
		t.Errorf("settleFailures must work on a copy: got %+v, stored %+v", settled[0], items[0])
	}
}
