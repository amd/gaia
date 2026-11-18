package test

import (
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/gaiaconnectors"
)

// connectorsCLIPath is the stand-in `gaia` that /connectors runs.
var connectorsCLIPath atomic.Pointer[string]

func init() {
	gaiaconnectors.Binary = func() (string, error) {
		if p := connectorsCLIPath.Load(); p != nil {
			return *p, nil
		}
		return "", errors.New("gaia CLI stubbed out by the test harness")
	}
}

// twoGoogleAccounts is `gaia connectors list --json` with two Google accounts,
// the second one's sign-in expired.
const twoGoogleAccounts = `[
 {"id": "google", "display_name": "Google", "type": "oauth_pkce", "category": "productivity",
  "tier": "core", "configured": true, "enabled": true, "account_id": "jane@example.com",
  "scopes": [], "activations": {},
  "accounts": [
   {"account_id": "1111", "email": "jane@example.com", "status": "ok", "is_default": true},
   {"account_id": "2222", "email": "jane.work@example.com", "status": "reauth_required", "is_default": false}]},
 {"id": "microsoft", "display_name": "Microsoft", "type": "oauth_pkce", "category": "productivity",
  "tier": "core", "configured": true, "enabled": true, "account_id": "jane@example.org",
  "scopes": [], "activations": {},
  "accounts": [{"account_id": "3333", "email": "jane@example.org", "status": "ok", "is_default": true}]}]`

// TestConnectorsShowsAndRemovesOneOfSeveralAccounts drives /connectors in the
// running TUI against a stand-in CLI: the list names each account, revoke
// signs nothing out, a bare remove asks which account, and a named remove
// disconnects only that one.
func TestConnectorsShowsAndRemovesOneOfSeveralAccounts(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stand-in CLI is a shell script")
	}
	dir := t.TempDir()
	argsLog := filepath.Join(dir, "calls")
	fixture := filepath.Join(dir, "list.json")
	writeStub(t, fixture, twoGoogleAccounts)
	cli := filepath.Join(dir, "gaia")
	writeStub(t, cli, `#!/bin/sh
echo "$*" >> '`+argsLog+`'
case "$2" in
  list) cat '`+fixture+`' ;;
  disconnect) echo "Removed jane.work@example.com from google; 1 other account still connected." ;;
esac
`)
	connectorsCLIPath.Store(&cli)
	t.Cleanup(func() { connectorsCLIPath.Store(nil) })

	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})
	tui.waitFor(map[string]any{"contains": "Ask anything"})

	send := func(text string) {
		tui.call(http.MethodPost, "/text", map[string]any{"text": text})
		tui.keys("enter")
	}

	send("/connectors")
	tui.waitFor(map[string]any{"contains": "Google (google)"})
	screen := tui.screen()
	for _, want := range []string{
		"connected, 2 accounts",
		"jane@example.com (default)",
		"jane.work@example.com (sign-in expired: /connectors reconnect google jane.work@example.com)",
		"Microsoft (microsoft) connected as jane@example.org",
		"/connectors add <id>",
		"/connectors remove <id> <address>",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the list is missing %q:\n%s", want, screen)
		}
	}
	saveCapture(t, tui, "connectors-list.svg")

	send("/connectors revoke google")
	tui.waitFor(map[string]any{"contains": "comes with signing in"})
	saveCapture(t, tui, "connectors-revoke-points-to-remove.svg")

	send("/connectors remove google")
	tui.waitFor(map[string]any{"contains": "Google has 2 accounts"})
	saveCapture(t, tui, "connectors-remove-choices.svg")

	send("/connectors remove google jane.work@example.com")
	tui.waitFor(map[string]any{"contains": "1 other account still connected"})
	saveCapture(t, tui, "connectors-remove-done.svg")

	raw, err := os.ReadFile(argsLog)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "connectors disconnect google --account jane.work@example.com\n") ||
		strings.Count(string(raw), "disconnect") != 1 {
		t.Fatalf("want exactly one disconnect of the named account, the CLI saw:\n%s", raw)
	}
}

// saveCapture writes the current frame as SVG to $GAIA_TUI_CAPTURE_DIR, when
// it is set, for a reviewer to look at.
func saveCapture(t *testing.T, tui *liveTUI, name string) {
	t.Helper()
	dir := os.Getenv("GAIA_TUI_CAPTURE_DIR")
	if dir == "" {
		return
	}
	req, err := http.NewRequest(http.MethodGet, tui.srv.BaseURL()+"/control/v1/screen?format=svg", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+tui.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	svg, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), svg, 0o644); err != nil {
		t.Fatal(err)
	}
}
