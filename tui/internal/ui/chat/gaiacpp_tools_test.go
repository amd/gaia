package chat

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// gaiacppTools mirrors tests/fixtures/gaiacpp_tools.json, which cpp/tests
// holds gaiacpp's own registry to.
type gaiacppTools struct {
	Tools []string `json:"tools"`
	Gated []string `json:"gated"`
}

func loadGaiacppTools(t *testing.T) gaiacppTools {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "tests", "fixtures", "gaiacpp_tools.json"))
	if err != nil {
		t.Fatalf("read the gaiacpp tool list: %v", err)
	}
	var tools gaiacppTools
	if err := json.Unmarshal(raw, &tools); err != nil {
		t.Fatalf("parse the gaiacpp tool list: %v", err)
	}
	if len(tools.Tools) == 0 || len(tools.Gated) == 0 {
		t.Fatal("the gaiacpp tool list is empty")
	}
	return tools
}

func TestEveryGaiacppToolIsNarratedInWords(t *testing.T) {
	for _, tool := range loadGaiacppTools(t).Tools {
		for _, args := range []string{``, `{"command":"ls","path":"a.txt","query":"q","pattern":"*.go","url":"https://example.com","name":"n","prompt":"a cat","ref":"HEAD","uri":"x://y"}`} {
			line := toolNarration(tool, json.RawMessage(args), "")
			if line == "" || strings.Contains(line, tool) || strings.HasPrefix(line, "Running "+strings.ReplaceAll(tool, "_", " ")) {
				t.Errorf("%s narrates as %q, want plain words", tool, line)
			}
		}
	}
}

func TestEveryGaiacppApprovalAsksAPlainQuestion(t *testing.T) {
	for _, tool := range loadGaiacppTools(t).Gated {
		title := components.ActionTitle(tool)
		if strings.Contains(title, tool) || strings.HasPrefix(title, "Allow ") {
			t.Errorf("%s asks %q, want its own plain question", tool, title)
		}
	}
	if got := components.ActionTitle("bash_execute"); got != components.ActionTitle("run_shell_command") {
		t.Errorf("bash_execute asks %q, want the shell tool's question", got)
	}
}

func TestAnUnknownOrConnectorToolStillAsksInWords(t *testing.T) {
	for _, tool := range []string{"mcp_github_create_issue", "frob_widget"} {
		if title := components.ActionTitle(tool); strings.Contains(title, "_") {
			t.Errorf("%s asks %q, want no raw tool name", tool, title)
		}
	}
}

func TestGaiacppFileCallsKeepTheirChangeForTheWorkLog(t *testing.T) {
	edit := describeCall("file_edit", json.RawMessage(`{"path":"proj/inventory.py","old_string":"a = 1","new_string":"a = 2"}`), "")
	if edit.Category != catEdit || edit.Change == nil {
		t.Fatalf("file_edit = %+v, want an edit with its change", edit)
	}
	write := describeCall("file_write", json.RawMessage(`{"path":"notes.md","content":"hello\n"}`), "")
	if write.Category != catWrite || write.Change == nil {
		t.Fatalf("file_write = %+v, want a write with its content", write)
	}
	if got := describeCall("bash_execute", json.RawMessage(`{"command":"pytest -q"}`), ""); got.Category != catShell || got.Command != "pytest -q" {
		t.Errorf("bash_execute = %+v, want the command", got)
	}
}

func TestARawPayloadPreviewIsNotShownAsTheResultLine(t *testing.T) {
	data := `{"content_hash":"ab12","replacements":1,"success":true}`
	line := toolResultDetail(event.CanonicalToolResultEvent{Tool: "file_edit", Data: json.RawMessage(data), Preview: data})
	if strings.Contains(line, "content_hash") || strings.Contains(line, "{") {
		t.Errorf("result line = %q, want words, not the payload", line)
	}
	if line := toolResultDetail(event.CanonicalToolResultEvent{Tool: "file_edit", Preview: "Changed 1 place in inventory.py"}); line != "Changed 1 place in inventory.py" {
		t.Errorf("result line = %q, want the agent's own preview", line)
	}
	long := `{"content_hash":"ab12","path":"proj/inventory.py","replacements":1,"success":true}`
	if line := toolResultDetail(event.CanonicalToolResultEvent{Tool: "file_edit", Data: json.RawMessage(long), Preview: long[:40] + "…"}); strings.Contains(line, "content_hash") {
		t.Errorf("result line = %q, want a cut payload hidden too", line)
	}
}

func TestACommandWhoseOutputIsJSONStillShowsItsOutput(t *testing.T) {
	for _, tool := range []string{"bash_execute", "run_shell_command"} {
		data := `{"cwd":"/w","exit_code":0,"stderr":"","stdout":"{\"a\":1}
","timed_out":false}`
		line := toolResultDetail(event.CanonicalToolResultEvent{Tool: tool, Data: json.RawMessage(data), Preview: `{"a":1}`})
		if line != `{"a":1}` {
			t.Errorf("%s result line = %q, want the command's own output", tool, line)
		}
	}
}
