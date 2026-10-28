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
	// Back to back, the settled attempt keeps its own row and one-line error:
	// folded into its retry as "x2", the error it recovered from would vanish.
	got := ansi.Strip(workRecord(t, m))
	if strings.Contains(got, "└ failed") || strings.Contains(got, "x2") ||
		!strings.Contains(got, "└ error, retried: Command failed (exit 1): python.exe: No module named pip") {
		t.Errorf("the same command retried and passing must settle it, error kept:\n%s", got)
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

// A file tool is recovered only by a success on the same file: clean.py
// failing is not fixed by report.py running, and back to back the failure
// must not fold under the other file's success and vanish.
func TestAFileToolFailureIsRecoveredOnlyByTheSameFile(t *testing.T) {
	red := colour(t)
	m := feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"clean.py"}`),
		result("run_python_file", scriptError),
		call("run_python_file", `{"file_path":"report.py"}`),
		result("run_python_file", `{"success":true}`),
		turnEnds,
	)
	record := workRecord(t, m)
	got := ansi.Strip(record)
	if strings.Contains(got, "retried") || !strings.Contains(got, "└ failed") ||
		!strings.Contains(got, "Length mismatch") {
		t.Errorf("another file's success recovered or hid the clean.py failure:\n%s", got)
	}
	if !strings.Contains(record, red) {
		t.Errorf("the unrecovered failure must be red")
	}

	// The same file under another spelling of its path is the same retry.
	m = feed(t, newTestChat(t),
		call("run_python_file", `{"file_path":"scripts\\clean.py"}`),
		result("run_python_file", scriptError),
		call("write_file", `{"file_path":"scripts/clean.py"}`),
		result("write_file", `{"success":true}`),
		call("run_python_file", `{"file_path":"./scripts/clean.py"}`),
		result("run_python_file", `{"success":true}`),
		turnEnds,
	)
	if got := ansi.Strip(workRecord(t, m)); !strings.Contains(got, "error, retried") ||
		strings.Contains(got, "failed") {
		t.Errorf("a retry of the same file must settle it:\n%s", got)
	}
}

func TestRetryKeyBoundaries(t *testing.T) {
	cases := []struct{ a, b string }{
		{`{"path":"a.txt"}`, `{"path":"b.txt"}`},
		{`{"command":"python -m pip"}`, `{"command":"python -c x"}`},
		{`{"file_path":"a.py"}`, `{}`},
	}
	for _, c := range cases {
		if retryKey("t", json.RawMessage(c.a)) == retryKey("t", json.RawMessage(c.b)) {
			t.Errorf("%s and %s must not share a retry key", c.a, c.b)
		}
	}
	if retryKey("t", json.RawMessage(`not json`)) != "" || retryKey("t", json.RawMessage(`{"url":"x"}`)) != "" {
		t.Errorf("args with nothing to match on must have no retry key")
	}
}

// shellTurn runs one failing command, then one passing command, and ends the turn.
func shellTurn(t *testing.T, failing, passing string) ChatModel {
	t.Helper()
	args := func(c string) string {
		b, err := json.Marshal(map[string]string{"command": c})
		if err != nil {
			t.Fatal(err)
		}
		return string(b)
	}
	return feed(t, newTestChat(t),
		call("run_shell_command", args(failing)),
		result("run_shell_command", `{"status":"error","error":"Command failed (exit 1): boom-from-the-first-call"}`),
		call("run_shell_command", args(passing)),
		result("run_shell_command", `{"status":"success","return_code":0}`),
		turnEnds,
	)
}

// Wrappers and flags must not hide what a command does: each pair is two
// different jobs, so the first failure stands, red, with its error on screen.
func TestAShellFailureIsNotRecoveredByADifferentJobBehindAWrapper(t *testing.T) {
	red := colour(t)
	pairs := [][2]string{
		{"npm run build", "npm run lint"},
		{"python -m pip install formulas", "python -m pytest"},
		{"cmd /c npm run build", "cmd /c npm run lint"},
		{`powershell -Command "npm run build"`, `powershell -Command "npm run lint"`},
		{"python /tmp/a.py", "python /tmp/b.py"},
	}
	for _, p := range pairs {
		record := workRecord(t, shellTurn(t, p[0], p[1]))
		got := ansi.Strip(record)
		if strings.Contains(got, "retried") || strings.Contains(got, "x2") ||
			!strings.Contains(got, "└ failed") || !strings.Contains(got, "boom-from-the-first-call") ||
			!strings.Contains(record, red) {
			t.Errorf("%q passing recovered or hid %q failing:\n%s", p[1], p[0], got)
		}
	}
}

// The same job retried with a different flag, quoting or path spelling does
// recover it, and the error it recovered from stays on screen.
func TestASameJobRetryRecoversAndKeepsItsError(t *testing.T) {
	red := colour(t)
	pairs := [][2]string{
		{"pip install formulas", "pip install --user formulas"},
		{"python -m pip install formulas", "python -m pip install --user formulas"},
		{"cmd /c npm run build", "cmd /C npm run build"},
		{`powershell -Command "npm run build"`, `powershell -NoProfile -Command "npm run build"`},
		{`C:\Python\python.exe clean.py`, "python clean.py"},
	}
	for _, p := range pairs {
		record := workRecord(t, shellTurn(t, p[0], p[1]))
		got := ansi.Strip(record)
		if !strings.Contains(got, "└ error, retried: Command failed (exit 1): boom-from-the-first-call") ||
			strings.Contains(got, "└ failed") || strings.Contains(record, red) {
			t.Errorf("%q passing should settle %q with its error kept:\n%s", p[1], p[0], got)
		}
	}
}

// A call with no file or command to match on is never marked retried: another
// URL or another query succeeding says nothing about the one that failed.
func TestAFailureWithNothingToMatchOnIsNeverRetried(t *testing.T) {
	red := colour(t)
	turns := []struct{ tool, failing, passing string }{
		{"fetch_url", `{"url":"https://a.example/x"}`, `{"url":"https://b.example/y"}`},
		{"query_data", `{"sql":"SELECT * FROM missing"}`, `{"sql":"SELECT 1"}`},
		{"query_data", `{"sql":"SELECT 1"}`, `{"sql":"SELECT 1"}`},
	}
	for _, c := range turns {
		m := feed(t, newTestChat(t),
			call(c.tool, c.failing),
			result(c.tool, `{"status":"error","error":"boom-from-the-first-call"}`),
			call(c.tool, c.passing),
			result(c.tool, `{"success":true}`),
			turnEnds,
		)
		record := workRecord(t, m)
		got := ansi.Strip(record)
		if strings.Contains(got, "retried") || !strings.Contains(got, "└ failed") ||
			!strings.Contains(got, "boom-from-the-first-call") || !strings.Contains(record, red) {
			t.Errorf("%s %s: a keyless failure was settled or hidden:\n%s", c.tool, c.failing, got)
		}
	}
}

// The copied text says what the screen says.
func TestCopiedWorkMatchesTheScreenForARetriedStep(t *testing.T) {
	m := shellTurn(t, "pip install formulas", "pip install --user formulas")
	var copied string
	for _, msg := range m.messages {
		if msg.Role == RoleWork {
			copied = msg.Content
		}
	}
	if strings.Contains(copied, " — failed") ||
		!strings.Contains(copied, "error, retried: Command failed (exit 1): boom-from-the-first-call") {
		t.Errorf("copied work disagrees with the screen:\n%s", copied)
	}

	m = shellTurn(t, "npm run build", "npm run lint")
	for _, msg := range m.messages {
		if msg.Role == RoleWork {
			copied = msg.Content
		}
	}
	if !strings.Contains(copied, "npm run build — failed") || strings.Contains(copied, "retried") {
		t.Errorf("a standing failure must still copy as failed:\n%s", copied)
	}
}

// A chained command is the whole chain: the shell tool tells the model to
// write `cd build && make`, so a later `cd build && ls` passing says nothing
// about make. A cd becomes the folder, and every pipe stage counts except a
// trailing output filter.
func TestAChainedCommandIsRecoveredOnlyByTheSameChain(t *testing.T) {
	red := colour(t)
	differ := [][2]string{
		{"cd build && make", "cd build && ls"},
		{"cd build; npm test", "cd build; npm ls"},
		{"make || echo done", "ls || echo done"},
		{"cd a && make", "cd b && make"},
		{"npm test | tail -5", "npm ls | tail -5"},
		{"cat data.csv | python process.py", "cat data.csv | head"},
		{"cat data.csv | python process.py", "cat data.csv | python report.py"},
		{"make | tee a.log", "make | tee b.log"},
		{`bash -c "cd build && make"`, `bash -c "cd build && ls"`},
		// A quoted ; is text, not a second command.
		{`git commit -m "fix; ok"`, `git push origin "main; ok"`},
	}
	for _, p := range differ {
		record := workRecord(t, shellTurn(t, p[0], p[1]))
		got := ansi.Strip(record)
		if strings.Contains(got, "retried") || !strings.Contains(got, "└ failed") ||
			!strings.Contains(got, "boom-from-the-first-call") || !strings.Contains(record, red) {
			t.Errorf("%q passing recovered or hid %q failing:\n%s", p[1], p[0], got)
		}
	}
	same := [][2]string{
		{"cd a && make", "cd a && make"},
		{"cd build && make", `cd "build" && make -j4`},
		{"npm test | tail -5", "npm test | tail -50"},
		{"cd build && make;", "cd build ;; && make"},
		{"make 2>&1 | tail", "make 2>&1"},
		{"cat data.csv | python process.py", "cat data.csv | python process.py | head -5"},
		{"git log | less", "git log | head -20 | more"},
		{`bash -c "cd build && make"`, `bash -lc 'cd build && make'`},
	}
	for _, p := range same {
		got := ansi.Strip(workRecord(t, shellTurn(t, p[0], p[1])))
		if !strings.Contains(got, "└ error, retried") || strings.Contains(got, "└ failed") {
			t.Errorf("%q passing should settle %q:\n%s", p[1], p[0], got)
		}
	}
}

// A flag's value is part of the job: `--target out pkgA` and `--target out
// pkgB` install different things. A flag with no value (--user) still does
// not change the job.
func TestAFlagValueIsPartOfTheJob(t *testing.T) {
	differ := [][2]string{
		{"pip install --target out pkgA", "pip install --target out pkgB"},
		{"pip install --target=outA pkgA", "pip install --target=outB pkgA"},
		{"pip install -r a.txt", "pip install -r b.txt"},
		// Spelled differently, so not provably the same job: stays failed.
		{"pip install --target=out pkgA", "pip install --target out pkgA"},
	}
	for _, p := range differ {
		got := ansi.Strip(workRecord(t, shellTurn(t, p[0], p[1])))
		if strings.Contains(got, "retried") || !strings.Contains(got, "└ failed") {
			t.Errorf("%q passing recovered %q failing:\n%s", p[1], p[0], got)
		}
	}
	got := ansi.Strip(workRecord(t, shellTurn(t, "pip install --target out pkgA", "pip install --user --target out pkgA")))
	if !strings.Contains(got, "└ error, retried") {
		t.Errorf("adding a valueless flag must still recover the same job:\n%s", got)
	}
}

func TestShellSegments(t *testing.T) {
	cases := map[string]string{
		`cd build && make`:        "cd build|make",
		`a; b || c & d`:           "a|b|c|d",
		`echo "a; b && c" | tail`: "echo a; b && c / tail",
		`make 2>&1 | tail`:        "make 2>&1 / tail",
		`a | | b |`:               "a / b",
		` ; ; make ;`:             "make",
		`git commit -m 'x | y'`:   "git commit -m x | y",
		`cmd /c "npm run build"`:  "cmd /c npm run build",
		`run ""`:                  "run ",
	}
	for in, want := range cases {
		var segs []string
		for _, stages := range shellSegments(in) {
			var parts []string
			for _, words := range stages {
				parts = append(parts, strings.Join(words, " "))
			}
			segs = append(segs, strings.Join(parts, " / "))
		}
		if got := strings.Join(segs, "|"); got != want {
			t.Errorf("shellSegments(%q) = %q, want %q", in, got, want)
		}
	}
	if commandKey(" ; && ", 0) != "" || retryKey("run_shell_command", json.RawMessage(`{"command":" ;; "}`)) != "" {
		t.Errorf("a command line with no command must have no retry key")
	}
}
