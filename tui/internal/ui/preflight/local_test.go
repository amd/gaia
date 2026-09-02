package preflight

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// isolateHome points the install-root lookup at a temp dir, so a developer box
// with a real ~/.gaia/agents cannot make a "nothing is installed" test pass or
// fail for reasons that have nothing to do with it.
func isolateHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)        // os.UserHomeDir on POSIX
	t.Setenv("USERPROFILE", home) // ... and on Windows
	return home
}

// stubGaiaInit replaces the `gaia init` binary resolution for the duration of
// the test, so nothing here spawns a real Python interpreter.
func stubGaiaInit(t *testing.T, fn func() (string, error)) {
	t.Helper()
	orig := gaiainit.Binary
	gaiainit.Binary = fn
	t.Cleanup(func() { gaiainit.Binary = orig })
}

func localCfg() Config { return Config{AgentID: "gaia", AgentName: "GAIA"}.withDefaults() }

// The common case for anyone who ran only the TUI binary. Before this, the
// launch had no gate at all and died at exec.
func TestBinaryRowNamesTheInstallerWhenNothingIsThere(t *testing.T) {
	isolateHome(t)
	r := localRunner{opts: LocalOptions{Binary: "gaia-agent-absent-fixture"}}

	row := r.checkBinary(context.Background(), localCfg())

	if row.State != StateFailed {
		t.Fatalf("a missing agent binary is %s, want failed", row.State.Word())
	}
	if row.Disposition != status.DispositionHalt {
		t.Error("a missing agent binary does not halt the launch")
	}
	// The three parts CLAUDE.md requires: what failed, what to do, where to look.
	if !strings.Contains(row.Detail, "gaia-agent-absent-fixture") {
		t.Errorf("the row does not name the missing program:\n%s", row.Detail)
	}
	if !strings.Contains(row.Detail, "Looked in:") {
		t.Errorf("the row does not say where it looked:\n%s", row.Detail)
	}
	if !strings.Contains(row.Remedy.Action, "installer") {
		t.Errorf("the remedy does not point at the installer: %q", row.Remedy.Action)
	}
	if !strings.Contains(row.Remedy.Action, catalog.InstallerURL) {
		t.Errorf("the remedy does not carry the installer URL: %q", row.Remedy.Action)
	}
	// `run:` means "type this". A URL there reads as a command to run.
	if row.Remedy.Command != "" {
		t.Errorf("the remedy put %q in the run-this slot", row.Remedy.Command)
	}
	if row.Remedy.Where == "" {
		t.Error("the remedy says nothing about where to look next")
	}
	// A TUI quietly fetching a ~90 MB binary over a path nothing verifies is
	// exactly the silent fallback the rules forbid.
	if row.Fix != FixNone {
		t.Errorf("the missing-binary row offers a one-key fix (%v); there is no verified download", row.Fix)
	}
	// The old text told the user to build from source or browse a catalog. Both
	// are wrong for a product that ships one agent with an installer.
	for _, gone := range []string{"gaia tui list", "gaia tui install", "build "} {
		if strings.Contains(row.Detail+row.Remedy.Action, gone) {
			t.Errorf("the row still says %q", gone)
		}
	}
}

// A file under the install root with no sentinel is NOT "go download it": the
// download already happened. `gaia-agent` is also the name of the frozen REST
// sidecar, so running it unverified is how #3062 fed uvicorn's log to a JSON
// scanner.
func TestBinaryRowTellsAnUnfinishedInstallApartFromAMissingOne(t *testing.T) {
	home := isolateHome(t)
	const name = "gaia-agent-unverified-fixture"
	dir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	file := name
	if runtime.GOOS == "windows" {
		file += ".exe"
	}
	if err := os.WriteFile(filepath.Join(dir, file), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}

	row := localRunner{opts: LocalOptions{Binary: name}}.checkBinary(context.Background(), localCfg())

	if row.State != StateFailed {
		t.Fatalf("an unverified install is %s, want failed", row.State.Word())
	}
	if !strings.Contains(row.Detail, catalog.SentinelName) {
		t.Errorf("the row does not name the missing sentinel:\n%s", row.Detail)
	}
	if !strings.Contains(row.Remedy.Command, "install") {
		t.Errorf("remedy command = %q, want a reinstall", row.Remedy.Command)
	}
	// Saying "not on this machine" here sends the user chasing a download that
	// already happened.
	if strings.Contains(row.Line, "not on this machine") {
		t.Errorf("an unfinished install reads as a missing one: %q", row.Line)
	}
}

// A --mock (or any entry carrying a full path) must still name the PROGRAM.
// "the installer ships C:/some/where/gaia-agent" is a claim about a path
// nobody has.
func TestTheMissingBinaryRowNamesTheProgramNotThePath(t *testing.T) {
	isolateHome(t)
	r := localRunner{opts: LocalOptions{Binary: filepath.Join("C:", "nope", "gaia-agent")}}

	row := r.checkBinary(context.Background(), localCfg())

	if !strings.Contains(row.Remedy.Action, "it ships gaia-agent alongside") {
		t.Errorf("the remedy names a path instead of the program: %q", row.Remedy.Action)
	}
}

// A forward-slash path is a path on Windows too. Testing only os.PathSeparator
// there sent "C:/tools/gaia-agent" down the search-by-name branch, which then
// named three places it had never looked.
func TestAForwardSlashPathIsNotSearchedForByName(t *testing.T) {
	isolateHome(t)
	r := localRunner{opts: LocalOptions{Binary: "C:/definitely/not/here/gaia-agent"}}

	row := r.checkBinary(context.Background(), localCfg())

	if strings.Contains(row.Detail, "your PATH") {
		t.Errorf("an explicit path was reported as missing from PATH:\n%s", row.Detail)
	}
	if !strings.Contains(row.Detail, "C:/definitely/not/here/gaia-agent") {
		t.Errorf("the row does not name the path it was given:\n%s", row.Detail)
	}
}

func TestBinaryRowPassesOnAResolvedBinary(t *testing.T) {
	isolateHome(t)
	dir := t.TempDir()
	name := "fixture-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}

	row := localRunner{opts: LocalOptions{Binary: path}}.checkBinary(context.Background(), localCfg())

	if row.State != StateOK {
		t.Fatalf("a resolved binary is %s, want ok: %s", row.State.Word(), row.Detail)
	}
	if !strings.Contains(row.Line, name) {
		t.Errorf("the row does not name what it found: %q", row.Line)
	}
}

// The whole point of the Runner seam: local and daemon must answer "Lemonade is
// down" with the SAME command. Two screens naming two different ways to start
// the same server is the drift this reuse exists to prevent.
func TestTheLemonadeRemedyIsSharedWithTheDaemonRunner(t *testing.T) {
	// A port nothing serves, so the probe is guaranteed to fail without
	// depending on whether this machine happens to run Lemonade.
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed {
		t.Fatalf("an unreachable Lemonade is %s, want failed", row.State.Word())
	}

	want := lemonadeStartRemedy()
	if row.Remedy != want {
		t.Errorf("the local runner's Lemonade remedy has drifted from the shared one:\n"+
			" local: %+v\nshared: %+v", row.Remedy, want)
	}
}

// --use-claude exists to avoid starting the local backend, so a down Lemonade
// must not refuse the launch. It is not a pass either: embeddings have no
// Anthropic equivalent.
func TestClaudeModeDoesNotLetADownLemonadeRefuseTheLaunch(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")

	row := localRunner{opts: LocalOptions{ClaudeMode: true}}.checkLemonade(context.Background(), localCfg())

	if row.State == StateFailed {
		t.Fatal("--use-claude was refused over a local server it deliberately does not start")
	}
	if row.State == StateOK {
		t.Fatal("a Lemonade that is not running reported as ok; embeddings still need it")
	}
	if row.Disposition != status.DispositionNotify {
		t.Errorf("disposition = %v, want notify — this holds nothing but must be said", row.Disposition)
	}
	if !strings.Contains(row.Detail, "memory") {
		t.Errorf("the row does not say what stops working without it:\n%s", row.Detail)
	}
}

func TestClaudeCredentialIsRequiredBeforeTheFirstMessage(t *testing.T) {
	t.Setenv(claudeAPIKeyEnv, "")
	t.Chdir(t.TempDir())

	row := localRunner{opts: LocalOptions{ClaudeMode: true}}.
		checkClaudeCredential(context.Background(), localCfg())

	if row.State != StateFailed {
		t.Fatalf("missing Claude credential is %s, want failed", row.State.Word())
	}
	if row.Disposition != status.DispositionHalt {
		t.Fatalf("disposition = %v, want halt", row.Disposition)
	}
	if row.Line != "not set" {
		t.Errorf("line = %q, want not set", row.Line)
	}
	for _, text := range []string{claudeAPIKeyEnv, "first message", ".env", "relaunch"} {
		if !strings.Contains(row.Detail+row.Remedy.Action+row.Raw, text) {
			t.Errorf("missing %q from credential guidance: %+v", text, row)
		}
	}
	if strings.Contains(row.Detail+row.Remedy.Action+row.Raw, "sk-ant-") {
		t.Error("credential guidance must not suggest or expose a token value")
	}
}

func TestClaudeCredentialAcceptsTheWorkingDirectoryDotenv(t *testing.T) {
	t.Setenv(claudeAPIKeyEnv, "")
	dir := t.TempDir()
	t.Chdir(dir)
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("# local fixture\nexport ANTHROPIC_API_KEY=sk-ant-from-dotenv\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	row := localRunner{opts: LocalOptions{ClaudeMode: true}}.
		checkClaudeCredential(context.Background(), localCfg())

	if row.State != StateOK {
		t.Fatalf("working-directory .env credential is %s, want ok: %+v", row.State.Word(), row)
	}
	if strings.Contains(row.Line+row.Detail+row.Remedy.Action+row.Raw, "sk-ant-from-dotenv") {
		t.Error("dotenv credential was echoed")
	}
}

func TestClaudeCredentialPassesWithoutEchoingTheSecret(t *testing.T) {
	const secret = "sk-ant-test-only"
	t.Setenv(claudeAPIKeyEnv, secret)

	row := localRunner{opts: LocalOptions{ClaudeMode: true}}.
		checkClaudeCredential(context.Background(), localCfg())

	if row.State != StateOK {
		t.Fatalf("set Claude credential is %s, want ok", row.State.Word())
	}
	if strings.Contains(row.Line+row.Detail+row.Remedy.Action+row.Raw, secret) {
		t.Error("credential row echoed the secret")
	}
}

func TestClaudeCredentialRowOnlyAppearsInClaudeMode(t *testing.T) {
	localRows := localRunner{}.Rows(localCfg())
	for _, row := range localRows {
		if row.Key == KeyClaudeCredential {
			t.Fatal("local mode unexpectedly added a Claude credential row")
		}
	}

	claudeRows := localRunner{opts: LocalOptions{ClaudeMode: true}}.Rows(localCfg())
	if len(claudeRows) != len(localRows)+1 {
		t.Fatalf("Claude mode has %d rows, local mode has %d; want one extra row", len(claudeRows), len(localRows))
	}
	if claudeRows[1].Key != KeyClaudeCredential {
		t.Fatalf("Claude row order = %q, want credential immediately after the binary", claudeRows[1].Key)
	}
}

func TestClaudeCheckStopsBeforeLocalProbesWhenCredentialIsMissing(t *testing.T) {
	isolateHome(t)
	t.Setenv(claudeAPIKeyEnv, "")
	t.Chdir(t.TempDir())
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")
	stubGaiaInit(t, func() (string, error) {
		t.Error("the model row was probed even though the Claude credential is missing")
		return "", errors.New("must not be called")
	})

	dir := t.TempDir()
	name := "fixture-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}

	rep := localRunner{opts: LocalOptions{Binary: path, ClaudeMode: true}}.
		Check(context.Background(), localCfg())

	blocker, ok := rep.Blocker()
	if !ok || blocker.Key != KeyClaudeCredential {
		t.Fatalf("blocker = %q, found=%v; want Claude credential", blocker.Key, ok)
	}
	if row, _ := rep.Find(KeyLemonade); row.State != StatePending {
		t.Errorf("Lemonade row is %s, want pending behind credential failure", row.State.Word())
	}
	if row, _ := rep.Find(KeyModel); row.State != StatePending {
		t.Errorf("model row is %s, want pending behind credential failure", row.State.Word())
	}
}

// Exit 2 is what an installed gaia older than `--check` returns for
// "unrecognized arguments". Reading it as "not ready" ran a full multi-minute
// `gaia init` on EVERY launch.
func TestAnUnansweredSetupCheckIsUnknownNotNotReady(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return exitStub(t, 2), nil })

	row := localRunner{}.checkModels(context.Background(), localCfg())

	if row.State == StateFailed {
		t.Fatal("an unanswered `gaia init --check` was read as a clean machine")
	}
	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown", row.State.Word())
	}
	if !strings.Contains(row.Detail, "could not be determined") {
		t.Errorf("the row does not say the question went unanswered:\n%s", row.Detail)
	}
	if row.Fix != FixNone {
		t.Errorf("an unanswered check offers to run setup (%v); nothing established it is needed", row.Fix)
	}
}

// Exit 1 IS the documented "not ready" answer, and the row has to offer the fix.
func TestExitOneMeansSetupIsNeeded(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return exitStub(t, 1), nil })

	row := localRunner{}.checkModels(context.Background(), localCfg())

	if row.State != StateFailed {
		t.Fatalf("state = %s, want failed", row.State.Word())
	}
	if row.Fix != FixRunSetup {
		t.Errorf("fix = %v, want FixRunSetup", row.Fix)
	}
	if !strings.Contains(row.Remedy.Command, "gaia init") {
		t.Errorf("remedy command = %q, want a gaia init", row.Remedy.Command)
	}
}

func TestExitZeroMeansReady(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return exitStub(t, 0), nil })

	if row := (localRunner{}).checkModels(context.Background(), localCfg()); row.State != StateOK {
		t.Fatalf("state = %s, want ok: %s", row.State.Word(), row.Detail)
	}
}

// The walk stops at the first failure, the same way the daemon walk does:
// "the models are not downloaded" is meaningless when the program that would
// use them is not on the machine.
func TestCheckStopsAtTheFirstFailure(t *testing.T) {
	isolateHome(t)
	// If this ran the model check it would spawn a real `gaia init`; the stub
	// makes that observable rather than merely slow.
	stubGaiaInit(t, func() (string, error) {
		t.Error("the model row was probed even though the agent binary is missing")
		return "", errors.New("must not be called")
	})

	rep := localRunner{opts: LocalOptions{Binary: "gaia-agent-absent-fixture"}}.
		Check(context.Background(), localCfg())

	if !rep.Blocked() {
		t.Fatal("a missing agent binary did not block the launch")
	}
	blocker, _ := rep.Blocker()
	if blocker.Key != KeyBinary {
		t.Errorf("blocker = %q, want the agent binary row", blocker.Key)
	}
	if row, _ := rep.Find(KeyModel); row.State != StatePending {
		t.Errorf("the model row is %s behind a failed binary row, want pending", row.State.Word())
	}
}

// Rows() is what the first frame is laid out from, so it has to match what
// Check eventually fills in — a screen that grows rows makes the user re-read it.
func TestRowsMatchWhatCheckProduces(t *testing.T) {
	isolateHome(t)
	r := localRunner{opts: LocalOptions{Binary: "gaia-agent-absent-fixture"}}

	blank := r.Rows(localCfg())
	filled := r.Check(context.Background(), localCfg()).Rows

	if len(blank) != len(filled) {
		t.Fatalf("Rows() lays out %d rows, Check produces %d", len(blank), len(filled))
	}
	for i := range blank {
		if blank[i].Key != filled[i].Key {
			t.Errorf("row %d: laid out %q, filled %q", i, blank[i].Key, filled[i].Key)
		}
	}
}

// Every non-OK row has to declare a Disposition, or a row nobody thought about
// silently proceeds instead of loudly halting — see Row.needsHalt.
func TestEveryNonOKLocalRowDeclaresADisposition(t *testing.T) {
	isolateHome(t)
	t.Setenv(claudeAPIKeyEnv, "")
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")
	stubGaiaInit(t, func() (string, error) { return exitStub(t, 1), nil })

	r := localRunner{opts: LocalOptions{Binary: "gaia-agent-absent-fixture"}}
	rows := []Row{
		r.checkBinary(context.Background(), localCfg()),
		r.checkClaudeCredential(context.Background(), localCfg()),
		r.checkLemonade(context.Background(), localCfg()),
		r.checkModels(context.Background(), localCfg()),
	}
	for _, row := range rows {
		if row.State == StateOK || row.State == StatePending {
			continue
		}
		if row.Disposition == status.DispositionUnset {
			t.Errorf("row %q is %s and declares no disposition", row.Key, row.State.Word())
		}
	}
}

// exitStub writes a script that does nothing but exit with code, and returns
// its path. It stands in for `gaia init`, so no test here spawns Python.
func exitStub(t *testing.T, code int) string {
	t.Helper()
	dir := t.TempDir()
	if runtime.GOOS == "windows" {
		path := filepath.Join(dir, "gaia-stub.bat")
		if err := os.WriteFile(path, []byte(fmt.Sprintf("@echo off\r\nexit /b %d\r\n", code)), 0o755); err != nil {
			t.Fatal(err)
		}
		return path
	}
	path := filepath.Join(dir, "gaia-stub.sh")
	if err := os.WriteFile(path, []byte(fmt.Sprintf("#!/bin/sh\nexit %d\n", code)), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}
