package gateway

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestSavePreservesKeysTheTUIDoesNotModel(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gateway.json")
	t.Setenv("GAIA_GATEWAY_FILE", path)
	// The Python side records learned model capabilities in this file. A save
	// from the TUI used to drop them, so the user paid the empty stream again
	// after every visit to this screen.
	seed := `{"base_url":"https://x/v1","enabled_models":["amd.a"],` +
		`"active_model":"amd.a","non_streaming_models":["amd.claude-opus-5"]}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}

	state, err := LoadState()
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if err := state.SetActive("amd.b").Save(); err != nil {
		t.Fatalf("Save: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("saved file is not JSON: %v", err)
	}
	if got["active_model"] != "amd.b" {
		t.Errorf("active_model = %v, want amd.b", got["active_model"])
	}
	learned, ok := got["non_streaming_models"].([]any)
	if !ok || len(learned) != 1 || learned[0] != "amd.claude-opus-5" {
		t.Errorf("non_streaming_models = %v, want it carried through", got["non_streaming_models"])
	}
}
