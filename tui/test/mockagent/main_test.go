package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The mock is what the TUI's end-to-end tests talk to, so if its keys drift
// from the real agent's the e2e suite goes on passing against a protocol the
// real agent no longer speaks.
func TestMockKeysMatchTheSharedStdioFixture(t *testing.T) {
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "stdio", "gaia_stdio_wire.json")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in fixture
	if err != nil {
		t.Fatalf("could not read the shared stdio wire fixture at %s: %v", path, err)
	}
	var fixture struct {
		Stdin struct {
			QueryKey     string                     `json:"query_key"`
			ControlKey   string                     `json:"control_key"`
			ControlVerbs map[string]json.RawMessage `json:"control_verbs"`
		} `json:"stdin"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("could not parse the shared stdio wire fixture: %v", err)
	}
	if controlKey != fixture.Stdin.ControlKey {
		t.Errorf("controlKey = %q, fixture says %q", controlKey, fixture.Stdin.ControlKey)
	}
	if queryKey != fixture.Stdin.QueryKey {
		t.Errorf("queryKey = %q, fixture says %q", queryKey, fixture.Stdin.QueryKey)
	}
	for _, verb := range []string{"cancel", "bypass"} {
		if _, ok := fixture.Stdin.ControlVerbs[verb]; !ok {
			t.Errorf("the mock handles %q, which the real agent does not know", verb)
		}
	}
}
