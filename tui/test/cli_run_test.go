package test

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/ui"
)

// altScreenEnter is the escape sequence Bubble Tea writes when it takes over the
// terminal. A one-shot that emits it is not a one-shot.
const altScreenEnter = "\x1b[?1049h"

// buildBinaries compiles the CLI and the mock agent into a temp dir, naming the
// mock `gaia-agent` so the catalog's PATH lookup finds it as the flagship. This
// exercises the real command the user runs, not an in-process shortcut.
func buildBinaries(t *testing.T) (gaiaBin, binDir string) {
	t.Helper()
	if testing.Short() {
		t.Skip("builds the CLI binary; skipped under -short")
	}

	binDir = t.TempDir()
	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	gaiaBin = filepath.Join(binDir, "gaia-tui"+suffix)
	mockBin := filepath.Join(binDir, "gaia-agent"+suffix)

	for target, out := range map[string]string{"./cmd/gaia": gaiaBin, "./test/mockagent": mockBin} {
		cmd := exec.Command("go", "build", "-o", out, target)
		cmd.Dir = repoTUIDir(t)
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("go build %s: %v\n%s", target, err, output)
		}
	}
	return gaiaBin, binDir
}

// oneShotEnv is the environment a gated one-shot needs to get past readiness:
// the mock agent on PATH, a `gaia` CLI whose `init --check` says "ready", and a
// Lemonade that answers /models. Every one of the three is a row the gate
// checks, and leaving one out is a legitimate refusal rather than a test bug —
// TestAMissingAgentBinaryHaltsAtPreflight covers that direction.
func oneShotEnv(t *testing.T, binDir string) []string {
	t.Helper()
	writeGaiaInitStub(t, binDir)
	return append(os.Environ(),
		"PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"),
		"LEMONADE_BASE_URL="+stubLemonade(t),
		"HOME="+t.TempDir(),
		"USERPROFILE="+t.TempDir(),
	)
}

// writeGaiaInitStub puts a `gaia` on PATH that answers `init --check` with
// exit 0 and nothing else. The real one downloads several GB.
func writeGaiaInitStub(t *testing.T, binDir string) {
	t.Helper()
	name, body := "gaia", "#!/bin/sh\nexit 0\n"
	if runtime.GOOS == "windows" {
		name, body = "gaia.bat", "@echo off\r\nexit /b 0\r\n"
	}
	if err := os.WriteFile(filepath.Join(binDir, name), []byte(body), 0o755); err != nil {
		t.Fatalf("write gaia stub: %v", err)
	}
}

// mockName is the mock agent's file name in binDir.
func mockName() string {
	if runtime.GOOS == "windows" {
		return "gaia-agent.exe"
	}
	return "gaia-agent"
}

// repoTUIDir returns the tui module root (this package lives in tui/test).
func repoTUIDir(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Dir(wd)
}

// TestRunQueryIsAGenuineOneShot is the acceptance test for `gaia tui run <id>
// --query`: the answer on stdout, progress on stderr, no alt screen, real exit
// code. Without all four it is useless from a script or from CI.
func TestRunQueryIsAGenuineOneShot(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "gaia", "--mock", filepath.Join(binDir, mockName()), "--query", "list the files")
	cmd.Env = oneShotEnv(t, binDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		t.Fatalf("run --query exited non-zero: %v\nstdout:\n%s\nstderr:\n%s", err, stdout.String(), stderr.String())
	}

	out := stdout.String()
	if strings.Contains(out, altScreenEnter) || strings.Contains(stderr.String(), altScreenEnter) {
		t.Fatal("run --query opened the alt screen; it is not a one-shot")
	}
	if !strings.Contains(out, "Based on your request") {
		t.Errorf("stdout does not carry the answer:\n%s", out)
	}
	// Progress belongs on stderr so `... --query X > answer.txt` captures
	// exactly the answer.
	if strings.Contains(out, "🔧") || strings.Contains(out, "  … ") {
		t.Errorf("progress leaked into stdout:\n%s", out)
	}
	if !strings.Contains(stderr.String(), "bash_execute") {
		t.Errorf("tool progress is missing from stderr:\n%s", stderr.String())
	}
}

// A tool's own error carries the remedy its author wrote — for the mailbox it
// is a `gaia connectors connect …` line explicitly marked "no Agent UI
// required". Relayed through the model it came back as "use Settings →
// Connections in the Agent UI", a GUI a terminal user cannot reach. It has to
// reach stderr verbatim, straight off the wire.
func TestFailedToolRemedyReachesStderrVerbatim(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "gaia", "--mock", filepath.Join(binDir, mockName()), "--query", "please fail the tool")
	cmd.Env = oneShotEnv(t, binDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	_ = cmd.Run()

	const remedy = "`gaia connectors connect google --scopes gmail.readonly --grant-agent installed:email`"
	if !strings.Contains(stderr.String(), remedy) {
		t.Errorf("the tool's remedy did not reach stderr:\n%s", stderr.String())
	}
	if !strings.Contains(stderr.String(), "CONNECTOR_ERROR") {
		t.Errorf("stderr does not carry the error code:\n%s", stderr.String())
	}
	if strings.Contains(stderr.String(), "unhandled event") {
		t.Errorf("an agent event was rendered as a placeholder instead of progress:\n%s", stderr.String())
	}
	// The remedy is progress, not the answer: `--query X > answer.txt` must
	// still capture exactly the answer.
	if strings.Contains(stdout.String(), "CONNECTOR_ERROR") {
		t.Errorf("the tool error leaked into stdout:\n%s", stdout.String())
	}
}

// --debug is documented as "enable debug logging to stderr" and produced output
// byte-identical to a run without it on the one-shot path — the path it is most
// needed on.
func TestDebugAddsDiagnosticsToTheOneShotPath(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	run := func(args ...string) string {
		cmd := exec.Command(gaiaBin, args...)
		cmd.Env = oneShotEnv(t, binDir)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		_ = cmd.Run()
		return stderr.String()
	}

	mock := filepath.Join(binDir, mockName())
	quiet := run("run", "gaia", "--mock", mock, "--query", "list the files")
	loud := run("run", "gaia", "--mock", mock, "--query", "list the files", "--debug")

	if !strings.Contains(loud, "[DEBUG]") {
		t.Fatalf("--debug produced no diagnostics on the one-shot path:\n%s", loud)
	}
	if len(loud) <= len(quiet) {
		t.Errorf("--debug output is no richer than the plain run:\n%s", loud)
	}
	for _, want := range []string{"one-shot: agent=gaia", "tool_result"} {
		if !strings.Contains(loud, want) {
			t.Errorf("--debug output is missing %q:\n%s", want, loud)
		}
	}
}

// A one-shot against an agent that does not exist must fail loudly and exit
// non-zero rather than print nothing and succeed.
func TestRunUnknownAgentExitsNonZero(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "not-a-real-agent", "--query", "hi")
	cmd.Env = oneShotEnv(t, binDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err == nil {
		t.Fatal("an unknown agent id exited 0")
	}
	if !strings.Contains(stderr.String(), "not-a-real-agent") {
		t.Errorf("the error does not name the agent:\n%s", stderr.String())
	}
}

// An unknown --agent has to name what DOES exist, and must not send the user
// to a screen that is gone.
func TestUnknownAgentErrorNamesTheIdsThatExist(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "not-a-real-agent", "--query", "hi")
	cmd.Env = oneShotEnv(t, binDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err == nil {
		t.Fatal("an unknown agent id exited 0")
	}

	text := stderr.String()
	if !strings.Contains(text, "gaia") || !strings.Contains(text, "email") {
		t.Errorf("the error does not name the ids that do exist:\n%s", text)
	}
	// The old text pointed at a browser to go find one in.
	if strings.Contains(text, "gaia tui list") {
		t.Errorf("the error still points at a hub browser that no longer exists:\n%s", text)
	}
}

// --model and --timeout were accepted and then dropped on the --subprocess
// path: RunChat is given neither, so both changed nothing at all. (--query is
// honoured there — it opens the chat and sends the first message.)
func TestChatSubprocessRefusesFlagsItCannotHonour(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	for _, flag := range []struct {
		name string
		args []string
	}{
		{"--model", []string{"--model", "some-model"}},
		{"--timeout", []string{"--timeout", "3s"}},
		// A persistent flag, not a local one — this pins that the refusal's
		// Changed() lookup sees flags inherited from the root command too.
		{"--use-claude", []string{"--use-claude"}},
	} {
		t.Run(flag.name, func(t *testing.T) {
			args := append([]string{"chat", "--subprocess", "/bin/echo"}, flag.args...)
			cmd := exec.Command(gaiaBin, args...)
			var stdout, stderr bytes.Buffer
			cmd.Stdout = &stdout
			cmd.Stderr = &stderr
			if err := cmd.Run(); err == nil {
				t.Fatalf("%s with --subprocess exited 0; it was accepted and dropped", flag.name)
			}
			text := stderr.String()
			if !strings.Contains(text, flag.name) {
				t.Errorf("the refusal does not name %s:\n%s", flag.name, text)
			}
			if !strings.Contains(text, "--agent") {
				t.Errorf("the refusal does not name the path that does support it:\n%s", text)
			}
		})
	}
}

// A one-line refusal followed by 20 lines of command listing pushes the error
// off a short terminal.
func TestFlagRefusalDoesNotDumpTheUsageBlock(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "--control-port", "4001")
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err == nil {
		t.Fatal("the reserved port was accepted")
	}

	text := stderr.String()
	if !strings.Contains(text, "reserved") {
		t.Fatalf("the refusal does not explain itself:\n%s", text)
	}
	if strings.Contains(text, "Available Commands:") {
		t.Errorf("a flag refusal printed the whole usage block:\n%s", text)
	}
}

// The subcommands this binary ships must be discoverable from --help. The
// hub browser's list/install/uninstall are deliberately NOT among them: GAIA
// ships one agent, and `gaia hub` (the Python CLI) owns installing the sidecar
// agents that are fetched.
func TestTheShippedSubcommandsExist(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	out, err := exec.Command(gaiaBin, "--help").CombinedOutput()
	if err != nil {
		t.Fatalf("gaia-tui --help: %v\n%s", err, out)
	}
	commands := commandNames(t, string(out))
	for _, want := range []string{"run", "status", "chat"} {
		if !commands[want] {
			t.Errorf("`gaia-tui --help` does not list the %q subcommand:\n%s", want, out)
		}
	}
	// Matched as a COMMAND, not as a substring: `status`'s own summary says
	// "what is installed", and a bare Contains("install") hits that.
	for _, gone := range []string{"list", "install", "uninstall", "hub"} {
		if commands[gone] {
			t.Errorf("`gaia-tui --help` still offers the %q subcommand, which is gone:\n%s", gone, out)
		}
	}
}

// commandNames parses cobra's "Available Commands:" block.
func commandNames(t *testing.T, help string) map[string]bool {
	t.Helper()
	names := map[string]bool{}
	inBlock := false
	for _, line := range strings.Split(help, "\n") {
		if strings.HasPrefix(line, "Available Commands:") {
			inBlock = true
			continue
		}
		if inBlock {
			if strings.TrimSpace(line) == "" {
				break
			}
			if fields := strings.Fields(line); len(fields) > 0 {
				names[fields[0]] = true
			}
		}
	}
	if len(names) == 0 {
		t.Fatalf("could not parse any subcommands out of --help:\n%s", help)
	}
	return names
}

// `run --help` promises "exit 0 on a final answer / 1 on an error", and that
// promise is the whole point of --query. A turn whose only tool call came back
// `{"ok": false, …}` and which then wrote an apology exited 0, so a caller's
// `gaia tui run … && next-step` fired over work that never happened.
func TestFailedToolExitsNonZeroEvenWithAnAnswer(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "gaia", "--mock", filepath.Join(binDir, mockName()), "--query", "please fail the tool")
	cmd.Env = oneShotEnv(t, binDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()

	if err == nil {
		t.Fatalf("a turn whose only tool failed exited 0\nstdout:\n%s\nstderr:\n%s",
			stdout.String(), stderr.String())
	}
	if !strings.Contains(stderr.String(), "exit 1") {
		t.Errorf("stderr does not explain why the turn failed:\n%s", stderr.String())
	}
	// The agent's answer still belongs on stdout — only the verdict changed.
	if !strings.Contains(stdout.String(), "could not reach") {
		t.Errorf("the answer was dropped from stdout:\n%s", stdout.String())
	}
}

// The ordinary successful run must stay exit 0 and stay quiet.
func TestSuccessfulToolRunStaysZeroAndQuiet(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "gaia", "--mock", filepath.Join(binDir, mockName()), "--query", "list the files")
	cmd.Env = oneShotEnv(t, binDir)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("a successful run exited non-zero: %v\nstderr:\n%s", err, stderr.String())
	}
	if strings.Contains(stderr.String(), "unverified") || strings.Contains(stderr.String(), "exit 1") {
		t.Errorf("a successful run was judged:\n%s", stderr.String())
	}
}

// One command's help printed the exit contract twice and the two disagreed:
// the Long text described exit 3 while the --query flag, one screen below,
// still promised "exit 0/1". A single `--help` invocation contradicted itself.
//
// CLAUDE.md's rule is that a functional change updates EVERY doc describing it,
// so this asserts the property rather than any one wording: whatever states the
// contract must state all of it.
func TestHelpStatesTheWholeExitContract(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	for _, command := range [][]string{
		{"run", "--help"},
		{"chat", "--help"},
	} {
		t.Run(strings.Join(command, " "), func(t *testing.T) {
			out, err := exec.Command(gaiaBin, command...).CombinedOutput()
			if err != nil {
				t.Fatalf("%v: %v\n%s", command, err, out)
			}
			text := string(out)

			// The stale shorthand, which promises there are only two outcomes.
			for _, stale := range []string{"exit 0/1", "exits 0/1"} {
				if strings.Contains(text, stale) {
					t.Errorf("help still promises %q, but a withheld action exits %d:\n%s",
						stale, ui.ExitApprovalRequired, text)
				}
			}
			// Every code the one-shot can return has to appear.
			for _, want := range []string{"0", "1", "3"} {
				if !strings.Contains(text, "exit "+want) && !strings.Contains(text, want+" needs approval") &&
					!strings.Contains(text, want+" failed") && !strings.Contains(text, want+" answered") {
					t.Errorf("help does not mention exit %s:\n%s", want, text)
				}
			}
		})
	}
}
