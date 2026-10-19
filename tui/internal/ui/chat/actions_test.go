// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

func argsOf(t *testing.T, v map[string]string) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// A description is model output, and it lands on the one line a user reads.
// Empty, overlong, control-laden and template text must never reach it as-is.
func TestADescriptionIsSanitisedOrRefused(t *testing.T) {
	for _, tc := range []struct {
		name, in, want string
	}{
		{"empty", "", ""},
		{"whitespace", "   \t ", ""},
		{"plain", "Install the spreadsheet library", "Install the spreadsheet library"},
		{"trailing stop dropped", "Install the spreadsheet library.", "Install the spreadsheet library"},
		{"ANSI stripped", "\x1b[31mDelete\x1b[0m the cache", "Delete the cache"},
		{"clear-screen stripped", "Read\x1b[2J the file", "Read the file"},
		{"C0 dropped", "Read\x07 the\x00 file", "Read the file"},
		{"C1 CSI dropped", "Read\u009b2J the file", "Read2J the file"},
		{"bidi override dropped", "Read \u202eelif eht", "Read elif eht"},
		{"newlines flattened", "Read the\nfile", "Read the file"},
		{"template objective", "objective", ""},
		{"template plan headline", "Following the plan to do everything the user asked", ""},
		{"template step", "Executing step 3 of the plan", ""},
		{"template prompt text", "A clear statement of what you're trying to achieve", ""},
		{"template plan list", "Plan: read_file → write_file", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := cleanDescription(tc.in); got != tc.want {
				t.Errorf("cleanDescription(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}

	long := strings.Repeat("Reconcile every ledger line ", 20)
	got := cleanDescription(long)
	if displayWidth(got) > descriptionMax || !strings.HasSuffix(got, "…") {
		t.Errorf("an overlong description was not cut to %d columns with a mark: %q", descriptionMax, got)
	}
}

// Where the words come from, best first: the model's description, the goal it
// announced, then a summary derived from the call. Each one only when the one
// above is missing or unusable.
func TestHeadlineFallbackOrder(t *testing.T) {
	code := "import pandas as pd\ndf = pd.read_excel('a.xlsx')\ndf.to_excel('b.xlsx')"
	withDesc := describeCall("run_python", argsOf(t, map[string]string{"code": code, "description": "Total delays by airport"}), "")
	noDesc := describeCall("run_python", argsOf(t, map[string]string{"code": code}), "")
	badDesc := describeCall("run_python", argsOf(t, map[string]string{"code": code, "description": "objective"}), "")

	for _, tc := range []struct {
		name string
		item ActivityItem
		want string
		src  int
	}{
		{"description wins over the goal", ActivityItem{Call: withDesc, Goal: "Build the workbook"}, "Total delays by airport", headDescription},
		{"goal when there is no description", ActivityItem{Call: noDesc, Goal: "Build the workbook"}, "Build the workbook", headGoal},
		{"a template description falls through to the goal", ActivityItem{Call: badDesc, Goal: "Build the workbook"}, "Build the workbook", headGoal},
		{"a template goal falls through to the derived summary", ActivityItem{Call: noDesc, Goal: "Following the plan to x"}, "Run a 3-line Python script that reads a.xlsx and writes b.xlsx", headDerived},
		{"a stage line is not a goal", ActivityItem{Call: noDesc, Goal: "Working out how to answer"}, "Run a 3-line Python script that reads a.xlsx and writes b.xlsx", headDerived},
		{"musing is not a goal", ActivityItem{Call: noDesc, Goal: "Hmm, should I check the totals?"}, "Run a 3-line Python script that reads a.xlsx and writes b.xlsx", headDerived},
		{"derived when there is nothing else", ActivityItem{Call: noDesc}, "Run a 3-line Python script that reads a.xlsx and writes b.xlsx", headDerived},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, src := headline(tc.item)
			if got != tc.want || src != tc.src {
				t.Errorf("headline = %q (source %d), want %q (source %d)", got, src, tc.want, tc.src)
			}
		})
	}
}

// Every tool type gets a summary from its parsed arguments — never a raw dump.
func TestDerivedSummariesForEachToolType(t *testing.T) {
	for _, tc := range []struct {
		tool string
		args map[string]string
		want string
	}{
		{"run_python", map[string]string{"code": "print(1)"}, "Run a Python one-liner"},
		{"run_python", map[string]string{"code": "import pandas as pd\nframes = [pd.read_csv('jan.csv'), pd.read_csv('feb.csv'), pd.read_csv('mar.csv')]\npd.concat(frames).to_excel('q1.xlsx')\n# done"},
			"Run a 3-line Python script that reads 3 spreadsheets and writes q1.xlsx"},
		{"run_python", map[string]string{"code": "with open('notes.md', 'w') as f:\n    f.write('hi')"}, "Run a 2-line Python script that writes notes.md"},
		{"execute_python_file", map[string]string{"file_path": "C:/work/reconcile.py"}, "Run reconcile.py"},
		{"run_shell_command", map[string]string{"command": "python -m pip install formulas --quiet"}, "Install formulas (pip)"},
		{"run_shell_command", map[string]string{"command": "pip install pandas openpyxl"}, "Install pandas, openpyxl (pip)"},
		{"run_shell_command", map[string]string{"command": "python -m pytest -q tests/"}, "Run the tests"},
		{"run_shell_command", map[string]string{"command": "cd C:/proj && git status"}, "Run git status"},
		{"run_shell_command", map[string]string{"command": "gh issue list --jq 'map(.x) | length'"}, "Run gh issue"},
		{"run_shell_command", map[string]string{"command": "ls -la data && head -5 data/a.csv"}, "List data (+1 more)"},
		{"run_shell_command", map[string]string{"command": "curl -sL https://www.fdic.gov/analysis/qbp?x=1 -o qbp.pdf"}, "Download from fdic.gov/analysis/qbp"},
		{"run_shell_command", map[string]string{"command": "LANG=C sort big.txt"}, "Run sort"},
		{"read_file", map[string]string{"file_path": "C:/contracts/contract_A.docx"}, "Read contract_A.docx"},
		{"write_file", map[string]string{"file_path": "/tmp/out/report.md", "content": "# R\n"}, "Write report.md"},
		{"edit_file", map[string]string{"file_path": "src/dates.py", "old_content": "a", "new_content": "b"}, "Edit dates.py"},
		{"web_search", map[string]string{"query": "FDIC Q2 2026 net income"}, "Search the web for 'FDIC Q2 2026 net income'"},
		{"fetch_page", map[string]string{"url": "https://www.bts.gov/browse-statistical-products-and-data?q=1"}, "Read bts.gov/browse-statistical-products-and-data"},
		{"load_skill", map[string]string{"name": "local-python-runner"}, "Load the local-python-runner skill"},
	} {
		t.Run(tc.tool+"/"+tc.want, func(t *testing.T) {
			got := describeCall(tc.tool, argsOf(t, tc.args), "").Derived
			if got != tc.want {
				t.Errorf("derived = %q, want %q", got, tc.want)
			}
			for _, raw := range []string{"{", "\"", "\\n"} {
				if strings.Contains(got, raw) {
					t.Errorf("the summary carries raw payload %q: %q", raw, got)
				}
			}
		})
	}
}

// A sidecar that only echoes "<label>: <raw arg>" says less than a derived
// summary; one that says what the call is for wins.
func TestASidecarEchoLosesToTheDerivedSummary(t *testing.T) {
	args := argsOf(t, map[string]string{"command": "python -m pip install formulas --quiet"})
	if got := describeCall("run_shell_command", args, "Running command: python -m pip install formulas --quiet").Derived; got != "Install formulas (pip)" {
		t.Errorf("an echo narration won: %q", got)
	}
	if got := describeCall("run_shell_command", args, "Running command: python -m pip in…").Derived; got != "Install formulas (pip)" {
		t.Errorf("a clipped echo narration won: %q", got)
	}
	if got := describeCall("run_shell_command", args, "Making the workbook formulas recalculate").Derived; got != "Make the workbook formulas recalculate" {
		t.Errorf("a narration that says what the call is for lost: %q", got)
	}
}

// The raw command is never the headline, but it is never lost either: folded
// it is one dim line, and Ctrl+O shows all of it.
func TestTheRawCommandIsReachableWithExpand(t *testing.T) {
	code := "import pandas as pd\ndf = pd.read_excel('flights.xlsx')\nprint(df.groupby('carrier').size())"
	m := sizedChat(t, 100, 40)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "run_python",
			Args: argsOf(t, map[string]string{"code": code, "description": "Count flights per carrier"})},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "run_python", Data: []byte(`{"success":true}`)},
	)
	folded := ansi.Strip(m.renderLiveRegion())
	if !strings.Contains(folded, "● Count flights per carrier") {
		t.Fatalf("the description is not the headline:\n%s", folded)
	}
	if strings.Contains(folded, "groupby") {
		t.Errorf("the raw code leaked into the folded view:\n%s", folded)
	}
	m.expandWork = true
	expanded := ansi.Strip(m.renderLiveRegion())
	for _, line := range strings.Split(code, "\n") {
		if !strings.Contains(expanded, line) {
			t.Errorf("Ctrl+O lost the code line %q:\n%s", line, expanded)
		}
	}
}

// The live status line says what the agent is doing now, in the same words.
func TestTheStatusLineLeadsWithWhatTheCallDoes(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: "run_shell_command",
		Args: argsOf(t, map[string]string{"command": "python -m pip install openpyxl --quiet --no-input --disable-pip-version-check"})})
	line := ansi.Strip(m.statusLine(m.now()))
	if !strings.Contains(line, "Install openpyxl (pip)") {
		t.Errorf("the status line does not say what is running: %q", line)
	}
	if strings.Contains(line, "--disable-pip-version-check") {
		t.Errorf("the raw command is the headline: %q", line)
	}
}

// A model-supplied path or query with escapes cannot reach the frame through a
// derived summary either.
func TestDerivedSummariesAreScrubbed(t *testing.T) {
	for tool, args := range map[string]map[string]string{
		"read_file":  {"file_path": "C:/x/evil\x1b[2J\u202e.docx"},
		"web_search": {"query": "a\x1b]8;;http://x\x07b"},
	} {
		got := describeCall(tool, argsOf(t, args), "").Derived
		if strings.ContainsAny(got, "\x1b\x07\u202e") {
			t.Errorf("%s: control bytes survived into %q", tool, got)
		}
	}
}

// A directory tree squeezed onto one row is not an outcome a person can read;
// folded it is dropped, and Ctrl+O keeps it.
func TestATreeDumpIsNotAFoldedOutcome(t *testing.T) {
	m := sizedChat(t, 120, 30)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "tree", Args: argsOf(t, map[string]string{"path": "C:/work/contracts"})},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "tree", Data: []byte(`{"summary":". |-- our_standard/ |   ` + "`" + `-- terms.docx"}`)},
	)
	folded := ansi.Strip(m.renderLiveRegion())
	if !strings.Contains(folded, "Look through contracts") || strings.Contains(folded, "|--") {
		t.Errorf("the folded view shows a tree dump:\n%s", folded)
	}
	m.expandWork = true
	if expanded := ansi.Strip(m.renderLiveRegion()); !strings.Contains(expanded, "|--") {
		t.Errorf("Ctrl+O lost the listing:\n%s", expanded)
	}
}
