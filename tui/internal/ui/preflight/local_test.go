package preflight

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

func TestCloudPreflightStillRequiresEmbeddingReadiness(t *testing.T) {
	for _, model := range []string{"fireworks.gemma-4-31b-it", "amd.gpt-4.1"} {
		t.Run(model, func(t *testing.T) {
			r := localRunner{opts: LocalOptions{Model: model}}
			if !r.skipChatModel() {
				t.Fatal("cloud setup would download the local chat model")
			}
			stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, setupJSON), nil })
			row := modelRow(r)
			if row.State != StateFailed || row.Fix != FixRunSetup || !strings.Contains(row.Remedy.Command, "--skip-chat-model") {
				t.Fatalf("missing embedder did not block with cloud-compatible setup: %+v", row)
			}
			stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, embedderLoadsJSON), nil })
			stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
				return []lemonade.Model{{ID: model, Recipe: "cloud"}}, nil
			})
			row = modelRow(r)
			if row.State != StateOK || !strings.Contains(row.Line, "embedder loads") || !strings.Contains(row.Line, model) {
				t.Fatalf("ready cloud state is incorrect: %+v", row)
			}
		})
	}
	if (localRunner{opts: LocalOptions{Model: "user.embeddinggemma-300m-GGUF"}}).pickedLocalModel() == "" {
		t.Fatal("a dotted local id was mistaken for cloud inference")
	}
}

func TestCloudPreflightNeedsAuthenticatedLemonadeRouter(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("Authorization") != "Bearer isolated-router-key" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		fmt.Fprint(w, `{"data":[]}`)
	}))
	t.Setenv(lemonadeBaseURLEnv, server.URL)
	t.Setenv("LEMONADE_API_KEY", "isolated-router-key")
	r := localRunner{opts: LocalOptions{Model: "fireworks.gemma-4-31b-it"}}
	if row := r.checkLemonade(context.Background(), localCfg()); row.State != StateOK {
		t.Fatalf("authenticated router was rejected: %+v", row)
	}
	server.Close()
	if row := r.checkLemonade(context.Background(), localCfg()); row.State != StateFailed {
		t.Fatalf("cloud chat was allowed without its Lemonade router: %+v", row)
	}
}

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
	downLemonadeOn(t, linuxProbeWithUnit())

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed {
		t.Fatalf("an unreachable Lemonade is %s, want failed", row.State.Word())
	}

	// The COMMAND and the docs link are what must never drift — sending two
	// screens to different start instructions for the same server is the bug
	// this reuse exists to prevent. The Action legitimately differs: only this
	// runner offers `f`, so only it leads with what that key does.
	want := lemonadeStartRemedy()
	if row.Remedy.Command != want.Command || row.Remedy.Where != want.Where {
		t.Errorf("the local runner's Lemonade remedy has drifted from the shared one:\n"+
			" local: %+v\nshared: %+v", row.Remedy, want)
	}
	if !strings.Contains(row.Remedy.Action, want.Action) {
		t.Errorf("the shared start instruction was dropped rather than prefixed:\n"+
			" local: %q\nshared: %q", row.Remedy.Action, want.Action)
	}
	if !strings.HasPrefix(row.Remedy.Action, "Press f and setup starts it") {
		t.Errorf("the row explains the manual route before the key that automates "+
			"it:\n%q", row.Remedy.Action)
	}
	if row.FirstRun {
		t.Error("an installed server that will not start was presented as a first-run step")
	}
}

// downLemonadeOn makes checkLemonade see a loopback server that is not
// answering, on a machine resolved through probe, with no GAIA-owned server
// recorded and nothing auto-starting.
func downLemonadeOn(t *testing.T, probe hostProbe) {
	t.Helper()
	t.Setenv("GAIA_HOME", t.TempDir())
	t.Setenv(lemonadeBaseURLEnv, "")
	t.Setenv(serverPathEnv, "")
	restoreProbe, restoreHost, restoreStart := probeLemonade, realHostProbe, tryAutoStartLemonade
	t.Cleanup(func() {
		probeLemonade, realHostProbe, tryAutoStartLemonade = restoreProbe, restoreHost, restoreStart
	})
	probeLemonade = func(context.Context) (string, bool, string) {
		return "http://localhost:13305/api/v1", false, "stub: nothing answering"
	}
	realHostProbe = func() hostProbe { return probe }
	tryAutoStartLemonade = func(context.Context) (bool, string, string) { return false, "", "" }
}

// #4449: on a new machine the first screen read as an error, and its copy said
// "setup installs it" and "it is not on this machine — run gaia init" at once.
// Nothing installed is a step, with one way forward.
func TestANewMachineGetsAStepNotAFailure(t *testing.T) {
	downLemonadeOn(t, fakeHostFor("linux", nil, nil, map[string]string{}))

	row := localRunner{}.checkLemonade(context.Background(), localCfg())

	if !row.FirstRun || row.Step != installServerStep {
		t.Fatalf("an uninstalled server is not a first-run step: %+v", row)
	}
	if row.State != StateFailed || row.Fix != FixRunSetup {
		t.Fatalf("the step must still block and carry setup: %+v", row)
	}
	if strings.Contains(row.Detail+row.Remedy.Action, "Already have it") {
		t.Errorf("the contradictory prefix is back: %q", row.Remedy.Action)
	}
}

// GAIA's own server is installed, so the row must not call it missing — the
// "not on this machine" remedy is for system installs. A stopped server can
// leave its state file behind, so installed-ness comes from the unpacked binary.
func TestAStoppedEmbeddedServerIsNotCalledMissing(t *testing.T) {
	downLemonadeOn(t, fakeHostFor("linux", nil, nil, map[string]string{}))
	home := os.Getenv("GAIA_HOME")
	dist := filepath.Join(home, "lemonade", "dist", "1")
	if err := os.MkdirAll(dist, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dist, "lemond"), nil, 0o755); err != nil {
		t.Fatal(err)
	}
	state := `{"pid": 1, "port": 13305, "api_key": "k", "version": "1"}`
	if err := os.WriteFile(filepath.Join(home, "lemonade", "state.json"), []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}

	row := localRunner{}.checkLemonade(context.Background(), localCfg())

	if row.FirstRun {
		t.Fatal("an installed GAIA server that is down was shown as a first-run step")
	}
	if strings.Contains(row.Remedy.Action, "not on this machine") {
		t.Errorf("the row calls an installed server missing: %q", row.Remedy.Action)
	}
	if row.Remedy.Command != "gaia lemonade embedded start" {
		t.Errorf("command = %q, want the embedded start", row.Remedy.Command)
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

	row := modelRow(localRunner{})

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

// Exit 1 IS the documented "not ready" answer: a first-run step with the fix,
// sized from what the model server reported.
func TestExitOneMeansSetupIsNeeded(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, setupJSON), nil })

	row := modelRow(localRunner{})

	if row.State != StateFailed || !row.FirstRun {
		t.Fatalf("state = %s first-run = %v, want a failed first-run step", row.State.Word(), row.FirstRun)
	}
	if row.Fix != FixRunSetup {
		t.Errorf("fix = %v, want FixRunSetup", row.Fix)
	}
	if !strings.Contains(row.Remedy.Command, "gaia init") {
		t.Errorf("remedy command = %q, want a gaia init", row.Remedy.Command)
	}
	if row.Step != "download the models (3.5 GB)" {
		t.Errorf("step = %q, want the reported sizes summed", row.Step)
	}
}

func TestExitZeroMeansReadyAndNamesTheChatModel(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, localLoadsJSON), nil })

	rep := localRunner{}
	row, chat, chatID := rep.verifyModels(context.Background(), localCfg())
	if chatID != "Gemma-4-E4B-it-GGUF" {
		t.Errorf("chat model id = %q — the chat header names it before the agent's first ping", chatID)
	}
	if row.State != StateOK {
		t.Fatalf("state = %s, want ok: %s", row.State.Word(), row.Detail)
	}
	if row.Line != "Gemma-4-E4B-it-GGUF (3.2 GB) and the embedder load" {
		t.Errorf("line = %q", row.Line)
	}
	if chat != "Gemma-4-E4B-it-GGUF (3.2 GB, on this machine)" {
		t.Errorf("chat = %q", chat)
	}
}

// #4449: "downloaded" passed while the embedder could not start, and chat then
// failed on its first turn. A model that will not load is a real failure —
// red, no setup key (setup would change nothing), and the error behind `d`.
func TestExitThreeIsARealFailureNotAStep(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 3, embedderFailsJSON), nil })

	row := modelRow(localRunner{})

	if row.State != StateFailed || row.FirstRun {
		t.Fatalf("a model that will not load is %s first-run=%v, want a real failure", row.State.Word(), row.FirstRun)
	}
	if row.Fix != FixNone {
		t.Errorf("fix = %v; re-running setup cannot load a downloaded model", row.Fix)
	}
	if !row.Optional {
		t.Error("an embedder failure refuses the launch; chat still works without it")
	}
	if !strings.Contains(row.Line, "user.embeddinggemma-300m-GGUF will not load") {
		t.Errorf("line = %q, want the model named", row.Line)
	}
	if !strings.Contains(row.Detail, "document search and memory") {
		t.Errorf("detail does not say what breaks: %q", row.Detail)
	}
	if !strings.Contains(row.Raw, "model_load_error") {
		t.Errorf("raw = %q, want the server's error", row.Raw)
	}

	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 3, chatFailsJSON), nil })
	if row := modelRow(localRunner{}); row.Optional {
		t.Error("a chat model that will not load was allowed to start the session")
	}
}

const (
	setupJSON = `{"ready": false, "stage": "setup", "reasons": ["x"], "models": [` +
		`{"id": "user.embeddinggemma-300m-GGUF", "role": "embedding", "size_gb": 0.3, "loaded": false, "error": null},` +
		`{"id": "Gemma-4-E4B-it-GGUF", "role": "chat", "size_gb": 3.2, "loaded": false, "error": null}]}`
	embedderLoadsJSON = `{"ready": true, "stage": null, "reasons": [], "models": [` +
		`{"id": "user.embeddinggemma-300m-GGUF", "role": "embedding", "size_gb": 0.3, "loaded": true, "error": null}]}`
	localLoadsJSON = `{"ready": true, "stage": null, "reasons": [], "models": [` +
		`{"id": "user.embeddinggemma-300m-GGUF", "role": "embedding", "size_gb": 0.3, "loaded": true, "error": null},` +
		`{"id": "Gemma-4-E4B-it-GGUF", "role": "chat", "size_gb": 3.2, "loaded": true, "error": null}]}`
	embedderFailsJSON = `{"ready": false, "stage": "load", "reasons": ["y"], "models": [` +
		`{"id": "user.embeddinggemma-300m-GGUF", "role": "embedding", "size_gb": 0.3, "loaded": false, "error": "model_load_error: llama-server failed to start"},` +
		`{"id": "Gemma-4-E4B-it-GGUF", "role": "chat", "size_gb": 3.2, "loaded": true, "error": null}]}`
	chatFailsJSON = `{"ready": false, "stage": "load", "reasons": ["y"], "models": [` +
		`{"id": "Gemma-4-E4B-it-GGUF", "role": "chat", "size_gb": 3.2, "loaded": false, "error": "model_load_error"}]}`
)

// jsonStub is a `gaia` that prints body as `gaia init --check --json` would,
// then exits with code.
func jsonStub(t *testing.T, code int, body string) string {
	t.Helper()
	dir := t.TempDir()
	out := filepath.Join(dir, "out.json")
	if err := os.WriteFile(out, []byte(body+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" {
		path := filepath.Join(dir, "gaia-stub.bat")
		script := fmt.Sprintf("@echo off\r\ntype \"%s\"\r\nexit /b %d\r\n", out, code)
		if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
		return path
	}
	path := filepath.Join(dir, "gaia-stub.sh")
	script := fmt.Sprintf("#!/bin/sh\ncat '%s'\nexit %d\n", out, code)
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
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
		modelRow(r),
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

// A down Lemonade is the commonest first-run failure, and Check stops at the
// FIRST failure — so when this row blocks, the model row below it is pending
// and cannot be focused. Withholding the setup key here left the screen with
// nothing to press and a command the user had to go type somewhere else.
func TestADownLemonadeOffersTheOneKeySetup(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed {
		t.Fatalf("an unreachable Lemonade is %s, want failed", row.State.Word())
	}
	if row.Fix != FixRunSetup {
		t.Errorf("fix = %v, want FixRunSetup — `gaia init` installs and starts "+
			"Lemonade, so this row is fixable from the screen", row.Fix)
	}
}

// The guard the comment on that Fix depends on: rows after the first failure
// are PENDING, so two rows can never offer setup at once.
//
// Asserting on the Fix fields directly would pass vacuously wherever `gaia` is
// absent from PATH (every CI runner): the model row would report StateUnknown,
// not StateFailed, so the count would be 1 whether or not the halt existed.
// Asserting the halt itself needs nothing external.
func TestCheckHaltsAtTheFirstFailureSoOnlyOneRowCanOfferSetup(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")

	dir := t.TempDir()
	name := "fixture-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}

	rep := localRunner{opts: LocalOptions{Binary: path}}.
		Check(context.Background(), localCfg())

	seenFailure := false
	for _, r := range rep.Rows {
		if seenFailure && r.State != StatePending {
			t.Errorf("row %q is %s after an earlier failure; the walk did not halt, "+
				"so two rows could offer setup at once:\n%s",
				r.Key, r.State.Word(), rep)
		}
		if r.State == StateFailed {
			seenFailure = true
		}
	}
	if !seenFailure {
		t.Fatalf("expected a failed row with Lemonade pointed at a dead port:\n%s", rep)
	}
}

// `gaia init` inherits the same bad LEMONADE_SERVER_PATH, so pressing f would
// fail every time — while the step that actually fixes it (unset the variable)
// sat below as the optional alternative.
func TestABadServerPathOverrideOffersNoSetupKey(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:9/api/v1")
	t.Setenv(serverPathEnv, filepath.Join(t.TempDir(), "not-here"))

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.Fix != FixNone {
		t.Errorf("fix = %v, want FixNone — setup cannot repair an override it "+
			"would inherit", row.Fix)
	}
}

// `gaia init` auto-detects remote mode from a non-loopback LEMONADE_BASE_URL and
// then refuses to install or start anything, so the key provably cannot work.
// The daemon runner already special-cases this; the two screens must not differ.
func TestARemoteLemonadeOffersNoSetupKey(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://192.168.1.50:13305/api/v1")

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed {
		t.Fatalf("an unreachable remote Lemonade is %s, want failed", row.State.Word())
	}
	if row.Fix != FixNone {
		t.Errorf("fix = %v, want FixNone — `gaia init` refuses to install or "+
			"start anything in remote mode", row.Fix)
	}
}

// installEmbedded unpacks a fake GAIA-owned server under GAIA_HOME, stopped.
func installEmbedded(t *testing.T) {
	t.Helper()
	dist := filepath.Join(os.Getenv("GAIA_HOME"), "lemonade", "dist", "2026.39.1")
	if err := os.MkdirAll(dist, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dist, "lemond.exe"), nil, 0o755); err != nil {
		t.Fatal(err)
	}
}

// `gaia lemonade embedded stop` deletes the state file. What is left is an
// installed server, not a new machine — and the row must not call it one.
func TestAStoppedEmbeddedInstallIsNotAFirstRun(t *testing.T) {
	downLemonadeOn(t, fakeHostFor("linux", nil, nil, map[string]string{}))
	installEmbedded(t)

	row := localRunner{}.checkLemonade(context.Background(), localCfg())

	if row.FirstRun || strings.Contains(row.Line, "not installed") {
		t.Fatalf("a stopped GAIA server was offered as an install step: %+v", row)
	}
	if row.Remedy.Command != "gaia lemonade embedded start" {
		t.Errorf("command = %q, want the embedded start", row.Remedy.Command)
	}
}

// The gate starts GAIA's own stopped server the way GAIA starts it, so a user
// who followed "stop it, then press r" is not sent to setup.
func TestAutoStartStartsAStoppedEmbeddedServer(t *testing.T) {
	downLemonadeOn(t, fakeHostFor("linux", nil, nil, map[string]string{}))
	installEmbedded(t)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = autoStartForTest
	stubGaiaInit(t, func() (string, error) { return exitStub(t, 0), nil })
	started := false
	probeLemonade = func(context.Context) (string, bool, string) {
		return "http://localhost:13305/api/v1", started, "stub"
	}
	probeCalls := 0
	inner := probeLemonade
	probeLemonade = func(ctx context.Context) (string, bool, string) {
		probeCalls++
		started = probeCalls > 1 // down before the start, up after it
		return inner(ctx)
	}

	row := localRunner{}.checkLemonade(context.Background(), localCfg())

	if row.State != StateOK || !strings.Contains(row.Line, "started for you") {
		t.Fatalf("a stopped GAIA server was not started: %+v", row)
	}
	if !strings.Contains(row.Raw, "lemonade embedded start") {
		t.Errorf("trace does not say how it was started: %q", row.Raw)
	}
}

// autoStartForTest is the real starter, captured before any test swaps it out.
var autoStartForTest = tryAutoStartLemonade

// modelRow is the model row as Check produces it.
func modelRow(r localRunner) Row {
	row, _, _ := r.verifyModels(context.Background(), localCfg())
	return row
}

func TestAServerThatStopsAnsweringIsAFaultNotAStep(t *testing.T) {
	stubGaiaInit(t, func() (string, error) {
		return jsonStub(t, 1, `{"ready": false, "stage": "server", "reasons": ["GAIA's Lemonade Server is installed but not running"], "models": []}`), nil
	})

	row := modelRow(localRunner{})

	if row.FirstRun || row.State != StateFailed {
		t.Fatalf("a server fault rendered as a first-run step: %+v", row)
	}
	if !strings.Contains(row.Detail, "installed but not running") {
		t.Errorf("detail = %q, want the reason", row.Detail)
	}
}

// A system Lemonade the TUI cannot start itself must not get GAIA's private one
// started beside it.
func TestAutoStartLeavesASystemInstallAlone(t *testing.T) {
	// A legacy CLI install: found, but with no form the TUI may spawn.
	legacy := fakeHostFor("linux", []string{"lemonade-server"}, nil, map[string]string{})
	if l := resolveLemonadeWith(legacy); !l.Found || canAutoStart(l) {
		t.Fatalf("fixture is not a found-but-human-only install: %+v", l)
	}
	downLemonadeOn(t, legacy)
	installEmbedded(t)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = autoStartForTest
	stubGaiaInit(t, func() (string, error) {
		t.Error("GAIA's server was started beside a system install")
		return "", errors.New("must not be called")
	})

	if started, _, trace := tryAutoStartLemonade(context.Background()); started || trace != "" {
		t.Errorf("auto-start acted on a machine only a human can start: %v %q", started, trace)
	}
}

// A local model picked in the TUI replaces the hardware default: setup must not
// download a default the user chose a smaller model to avoid, the check loads
// the pick instead, and a pick that is missing is theirs to fetch, not setup's.
func TestAPickedLocalModelReplacesTheHardwareDefault(t *testing.T) {
	r := localRunner{opts: LocalOptions{Model: "Gemma-4-E4B-it-GGUF"}}
	if !r.skipChatModel() || !strings.Contains(gaiainit.RunCommand(r.skipChatModel()), "--skip-chat-model") {
		t.Fatal("setup would still download the hardware default chat model")
	}
	if r.verifySkipsChatModel() || r.localChatModel() != "Gemma-4-E4B-it-GGUF" {
		t.Fatalf("the check must load the picked model: skip=%v model=%q", r.verifySkipsChatModel(), r.localChatModel())
	}

	orig := downloadedLocalModels
	t.Cleanup(func() { downloadedLocalModels = orig })
	stubGaiaInit(t, func() (string, error) {
		return jsonStub(t, 1, `{"ready": false, "stage": "setup", "reasons": ["chat model not downloaded"], "models": []}`), nil
	})

	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) { return nil, nil }
	row := modelRow(r)
	if row.State != StateFailed || row.Fix != FixNone || !strings.Contains(row.Line, "not downloaded") {
		t.Fatalf("a missing pick was not reported, or setup was offered for it: %+v", row)
	}

	// Lemonade lists a user. model without its prefix; a downloaded pick leaves
	// only the embedder to set up.
	r = localRunner{opts: LocalOptions{Model: "user.Qwen3.8-Flash-Next-GGUF"}}
	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "Qwen3.8-Flash-Next-GGUF", Downloaded: true}}, nil
	}
	if row := modelRow(r); !row.FirstRun || row.Fix != FixRunSetup {
		t.Fatalf("user.-prefixed pick not matched to its listed id: %+v", row)
	}
}

func stubListedCloudModels(t *testing.T, fn func(context.Context, string) ([]lemonade.Model, error)) {
	t.Helper()
	orig := listedCloudModels
	listedCloudModels = fn
	t.Cleanup(func() { listedCloudModels = orig })
}

// Setup can move GAIA onto its own Lemonade after the provider was connected on
// another one. The gate said "ready" and the first turn failed with "Unknown
// Lemonade model"; it must stop here and say how to reconnect.
func TestAGateDoesNotPassACloudModelItsServerCannotServe(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, embedderLoadsJSON), nil })
	r := localRunner{opts: LocalOptions{Model: "amd.DeepSeek-V4.1-Flash"}}

	stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
		if provider != "amd" {
			t.Errorf("asked for provider %q, want amd", provider)
		}
		return nil, nil
	})
	row, chat, _ := r.verifyModels(context.Background(), localCfg())
	if row.State != StateFailed || row.Disposition != status.DispositionHalt || chat != "" {
		t.Fatalf("an unservable cloud model passed the gate: %+v chat=%q", row, chat)
	}
	if !strings.Contains(row.Remedy.Action, "Press p") || !strings.Contains(row.Remedy.Action, lemonade.Label("amd")) {
		t.Errorf("remedy %q does not say how to reconnect", row.Remedy.Action)
	}

	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "amd.deepseek-v4.1-flash", Recipe: "cloud"}}, nil
	})
	if row, chat, _ := r.verifyModels(context.Background(), localCfg()); row.State != StateOK || chat == "" {
		t.Fatalf("a served cloud model was refused: %+v chat=%q", row, chat)
	}

	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return nil, errors.New("connection refused")
	})
	if row := modelRow(r); row.State != StateUnknown || row.Disposition != status.DispositionNotify {
		t.Fatalf("an unanswered listing must be reported as unknown, not failed or passed: %+v", row)
	}
}
