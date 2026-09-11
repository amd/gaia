package preflight

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// GAIA's embedded Lemonade generates an API key and writes it to its state
// file. An unauthenticated probe gets 401 from it, which this screen reported
// as "Lemonade not running" — while the server was healthy and serving — and
// then offered a second install that would fight for the same port.
func TestLemonadeAPIKey(t *testing.T) {
	writeState := func(t *testing.T, key string) {
		t.Helper()
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		dir := filepath.Join(home, ".gaia", "lemonade")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		body, _ := json.Marshal(map[string]any{"pid": 1, "port": 13305, "api_key": key})
		if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	t.Run("reads the embedded server's key", func(t *testing.T) {
		t.Setenv("LEMONADE_API_KEY", "")
		writeState(t, "from-state-file")
		if got := lemonadeAPIKey(); got != "from-state-file" {
			t.Fatalf("got %q, want the key from state.json", got)
		}
	})

	t.Run("an explicit env var wins", func(t *testing.T) {
		writeState(t, "from-state-file")
		t.Setenv("LEMONADE_API_KEY", "from-env")
		if got := lemonadeAPIKey(); got != "from-env" {
			t.Fatalf("got %q, want the explicitly configured credential", got)
		}
	})

	t.Run("no state file and no env is not an error", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LEMONADE_API_KEY", "")
		if got := lemonadeAPIKey(); got != "" {
			t.Fatalf("got %q, want empty for an unauthenticated server", got)
		}
	})

	t.Run("a malformed state file is not fatal", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LEMONADE_API_KEY", "")
		dir := filepath.Join(home, ".gaia", "lemonade")
		_ = os.MkdirAll(dir, 0o755)
		_ = os.WriteFile(filepath.Join(dir, "state.json"), []byte("{not json"), 0o600)
		if got := lemonadeAPIKey(); got != "" {
			t.Fatalf("got %q, want empty rather than a panic", got)
		}
	})
}
