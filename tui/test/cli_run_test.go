package test

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// altScreenEnter is the escape sequence Bubble Tea writes when it takes over the
// terminal. A one-shot that emits it is not a one-shot.
const altScreenEnter = "\x1b[?1049h"

// buildBinaries compiles the CLI and the mock agent into a temp dir, naming the
// mock `gaia-bash` so the catalog's PATH lookup finds it. This exercises the
// real command the user runs, not an in-process shortcut.
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
	gaiaBin = filepath.Join(binDir, "gaia"+suffix)
	mockBin := filepath.Join(binDir, "gaia-bash"+suffix)

	for target, out := range map[string]string{"./cmd/gaia": gaiaBin, "./test/mockagent": mockBin} {
		cmd := exec.Command("go", "build", "-o", out, target)
		cmd.Dir = repoTUIDir(t)
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("go build %s: %v\n%s", target, err, output)
		}
	}
	return gaiaBin, binDir
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

	cmd := exec.Command(gaiaBin, "run", "bash", "--query", "list the files")
	cmd.Env = append(os.Environ(), "PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
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

// A one-shot against an agent that does not exist must fail loudly and exit
// non-zero rather than print nothing and succeed.
func TestRunUnknownAgentExitsNonZero(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "not-a-real-agent", "--query", "hi")
	cmd.Env = append(os.Environ(), "PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
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

// The unknown-id error used to print every id in the catalog as "known ids",
// which reads as a menu of things to run — and 12 of the 13 could not run.
// Picking one answered "build it" for an agent nothing publishes.
func TestUnknownAgentErrorSeparatesRunnableFromNotRunnable(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	cmd := exec.Command(gaiaBin, "run", "not-a-real-agent", "--query", "hi")
	cmd.Env = append(os.Environ(), "PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err == nil {
		t.Fatal("an unknown agent id exited 0")
	}

	text := stderr.String()
	if strings.Contains(text, "known ids") {
		t.Errorf("the error still presents every catalog id as runnable:\n%s", text)
	}
	if !strings.Contains(text, "gaia tui list") {
		t.Errorf("the error does not say how to find a runnable agent:\n%s", text)
	}
	if !strings.Contains(text, "Not runnable here") {
		t.Errorf("the error does not separate what cannot run:\n%s", text)
	}
}

// --model and --timeout were accepted and then dropped on the --subprocess
// path: RunChat is given neither, so both changed nothing at all. (--query is
// honoured there — it opens the chat and sends the first message.)
func TestChatSubprocessRefusesFlagsItCannotHonour(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	for _, flag := range []struct{ name, value string }{
		{"--model", "some-model"},
		{"--timeout", "3s"},
	} {
		t.Run(flag.name, func(t *testing.T) {
			cmd := exec.Command(gaiaBin, "chat", "--subprocess", "/bin/echo", flag.name, flag.value)
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

// The subcommands the port added must exist and be discoverable from --help.
func TestHubSubcommandsExist(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	out, err := exec.Command(gaiaBin, "--help").CombinedOutput()
	if err != nil {
		t.Fatalf("gaia --help: %v\n%s", err, out)
	}
	for _, want := range []string{"list", "install", "uninstall", "run", "status", "hub", "chat"} {
		if !strings.Contains(string(out), want) {
			t.Errorf("`gaia --help` does not list the %q subcommand:\n%s", want, out)
		}
	}
}

// --trust must be opt-in and documented; a user who has not read the docs has
// to be able to find it from --help.
func TestInstallHelpDocumentsTheTrustFlag(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	out, err := exec.Command(gaiaBin, "install", "--help").CombinedOutput()
	if err != nil {
		t.Fatalf("gaia install --help: %v\n%s", err, out)
	}
	text := string(out)
	if !strings.Contains(text, "--trust") {
		t.Errorf("install --help does not mention --trust:\n%s", text)
	}
	if !strings.Contains(text, "third-party code") {
		t.Errorf("install --help does not say what --trust means:\n%s", text)
	}
}
