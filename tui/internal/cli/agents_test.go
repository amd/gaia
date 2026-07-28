package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// writeSentinel plants an install record the way gaia.hub.installer does.
func writeSentinel(t *testing.T, root, id, version string) {
	t.Helper()
	dir := filepath.Join(root, id)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	raw, err := json.Marshal(catalog.InstalledRecord{ID: id, Version: version, Language: "python"})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, catalog.SentinelName), raw, 0o600); err != nil {
		t.Fatalf("write sentinel: %v", err)
	}
}

// `--installed` documents "no daemon, no network". It went through the daemon,
// so on a machine with none it failed with "could not start the GAIA daemon" —
// the offline flag both required a daemon and tried to launch one.
func TestListInstalledReadsDiskWithoutADaemon(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // Windows
	// No daemon home, no daemon, no network: if this path touches one it fails.
	t.Setenv("GAIA_DAEMON_HOME", filepath.Join(home, "no-daemon-here"))
	writeSentinel(t, filepath.Join(home, ".gaia", "agents"), "email", "0.5.0")

	var out, errW bytes.Buffer
	if err := runListInstalled(&out, &errW); err != nil {
		t.Fatalf("list --installed: %v", err)
	}

	text := out.String()
	if !strings.Contains(text, "email") || !strings.Contains(text, "0.5.0") {
		t.Errorf("the installed agent is missing from the output:\n%s", text)
	}
	if strings.Contains(errW.String(), "daemon") {
		t.Errorf("--installed reported a daemon problem for a local read:\n%s", errW.String())
	}
}

// Nothing installed is a normal answer, and it has to say what to do next.
func TestListInstalledOnAFreshMachineSaysWhatToDo(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)

	var out, errW bytes.Buffer
	if err := runListInstalled(&out, &errW); err != nil {
		t.Fatalf("list --installed: %v", err)
	}

	if !strings.Contains(out.String(), "gaia tui install") {
		t.Errorf("an empty list does not name the command that fills it:\n%s", out.String())
	}
}

// A sentinel that cannot be read makes an installed agent vanish, which looks
// exactly like "never installed". It must be reported, not dropped.
func TestListInstalledReportsAnUnreadableSentinel(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	dir := filepath.Join(home, ".gaia", "agents", "email")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, catalog.SentinelName), []byte("{broken"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	var out, errW bytes.Buffer
	err := runListInstalled(&out, &errW)

	if !strings.Contains(errW.String(), "email") {
		t.Errorf("a corrupt sentinel was swallowed:\nstdout=%s\nstderr=%s", out.String(), errW.String())
	}
	// Exit non-zero too: a script cannot see stderr text, and a short list looks
	// exactly like a correct one.
	if err == nil {
		t.Error("a hidden install record still exited 0")
	}
}

// runListInstalled drives the REAL `list --installed` command, so the test
// covers the routing as well as the rendering — the bug was that --installed
// went through the daemon at all.
func runListInstalled(out, errW *bytes.Buffer) error {
	rootCmd.SetOut(out)
	rootCmd.SetErr(errW)
	rootCmd.SetArgs([]string{"list", "--installed"})
	defer func() {
		rootCmd.SetArgs(nil)
		listInstalledOnly = false
	}()
	return rootCmd.Execute()
}

// "installed … (needs --trust)" reads as an install that never finished.
func TestListDoesNotAskForTrustOnAnInstalledAgent(t *testing.T) {
	var out bytes.Buffer
	printCatalog(&out, &catalog.HubCatalog{Agents: []catalog.HubEntry{{
		ID: "email", InstalledVersion: "0.5.0", LatestVersion: "0.5.0",
		Installed: true, Supervised: true, SecurityTier: "experimental",
	}}})

	if strings.Contains(out.String(), "needs --trust") {
		t.Errorf("an installed agent still asks for a trust opt-in:\n%s", out.String())
	}
}

func TestListStillAsksForTrustOnANotInstalledAgent(t *testing.T) {
	var out bytes.Buffer
	printCatalog(&out, &catalog.HubCatalog{Agents: []catalog.HubEntry{{
		ID: "email", LatestVersion: "0.5.0", Supervised: true, SecurityTier: "experimental",
	}}})

	if !strings.Contains(out.String(), "needs --trust") {
		t.Errorf("a non-verified agent that is not installed hides the trust opt-in:\n%s", out.String())
	}
}
