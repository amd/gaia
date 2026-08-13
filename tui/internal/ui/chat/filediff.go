package chat

import (
	"encoding/json"
	"strings"
)

// fileDiffResult is the subset of a file-editing tool's result payload the
// diff card needs. GAIA's file-editing tools (write_file, edit_file,
// write_python_file, edit_python_file, write_markdown_file, replace_function,
// generate_diff -- src/gaia/agents/tools/file_io_tools.py, backed by the
// shared gaia.agents.tools.diff_utils helper) all return this same
// status-based envelope for EVERY text file, not just Python or any other
// single language. The envelope predates the render-card contract, so a
// diff card is built here from `diff`/`status` rather than through
// `tool_result.render` — see filediff.go's package-level doc note in
// cards.go.
type fileDiffResult struct {
	Status   string `json:"status"`
	FilePath string `json:"file_path"`
	Diff     string `json:"diff"`
	IsBinary bool   `json:"is_binary"`
}

// diffCardData reports whether a tool_result's raw data carries a renderable
// text-file diff, and if so returns the `diff` card's
// `{title, unified}` payload (contract §4.3) built from it.
//
// Deliberately tool-name-agnostic: any tool_result whose data has a
// non-empty `diff` string on a non-error, non-binary result renders a card,
// so a future file-editing tool that follows the same convention gets the
// same card for free without another entry in a lookup table.
func diffCardData(data json.RawMessage) (json.RawMessage, bool) {
	if len(data) == 0 {
		return nil, false
	}
	var r fileDiffResult
	if err := json.Unmarshal(data, &r); err != nil {
		return nil, false
	}
	if r.Status == "error" || r.IsBinary {
		return nil, false
	}
	if strings.TrimSpace(r.Diff) == "" {
		return nil, false
	}

	title := r.FilePath
	if title == "" {
		title = "file"
	}
	payload, err := json.Marshal(struct {
		Title   string `json:"title"`
		Unified string `json:"unified"`
	}{Title: title, Unified: r.Diff})
	if err != nil {
		return nil, false
	}
	return payload, true
}
