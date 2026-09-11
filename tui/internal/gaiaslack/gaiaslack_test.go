package gaiaslack

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// stubGaia installs a fake `gaia` binary that prints what the test wants and
// exits with the given code. Substituting Binary rather than PATH keeps the
// real CLI (and its multi-second Python start-up) out of these tests.
func stubGaia(t *testing.T, stdout string, exitCode int) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("the shell stub is POSIX-only; the code under test is not")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "gaia")
	script := "#!/bin/sh\ncat <<'EOF'\n" + stdout + "\nEOF\nexit " +
		string(rune('0'+exitCode)) + "\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("could not write the gaia stub: %v", err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })
}

func TestQueryReadsEveryField(t *testing.T) {
	stubGaia(t, `{"slack_installed": true, "configured": true,
	 "onboarding_state": "connected", "team_name": "Acme",
	 "should_offer_setup": false, "running": true}`, 0)

	status, err := Query(context.Background())
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if !status.SlackInstalled || !status.Configured || !status.Running {
		t.Errorf("booleans did not survive the round trip: %+v", status)
	}
	if status.TeamName != "Acme" || status.OnboardingState != "connected" {
		t.Errorf("strings did not survive the round trip: %+v", status)
	}
	if status.ShouldOfferSetup {
		t.Error("ShouldOfferSetup must stay false")
	}
}

func TestQueryToleratesABannerBeforeTheJSON(t *testing.T) {
	// The CLI logs to stderr, but a dependency printing to stdout would
	// otherwise turn a working probe into "not set up".
	stubGaia(t, "Some library said something\n{\"slack_installed\": true}", 0)

	status, err := Query(context.Background())
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if !status.SlackInstalled {
		t.Error("the JSON after the banner was not parsed")
	}
}

func TestAFailedProbeIsUnansweredNotANegative(t *testing.T) {
	// An older gaia exits 2 for an unrecognized subcommand. Reading that as
	// "Slack is not set up" would offer setup on a machine that cannot run it.
	stubGaia(t, "usage: gaia [-h] ...\ngaia: error: argument command: invalid choice", 2)

	_, err := Query(context.Background())
	if err == nil {
		t.Fatal("a non-zero exit must not be reported as a clean answer")
	}
	if !errors.Is(err, ErrUnanswered) {
		t.Errorf("error must wrap ErrUnanswered, got %v", err)
	}
	if !strings.Contains(err.Error(), "invalid choice") {
		t.Errorf("the error must quote what GAIA said, got %v", err)
	}
}

func TestUnparseableOutputIsUnanswered(t *testing.T) {
	stubGaia(t, "not json at all", 0)

	_, err := Query(context.Background())
	if !errors.Is(err, ErrUnanswered) {
		t.Errorf("garbage output must be unanswered, got %v", err)
	}
}

func TestAMissingGaiaNamesTheInstallCommand(t *testing.T) {
	original := Binary
	Binary = func() (string, error) { return "", errors.New("not on PATH") }
	t.Cleanup(func() { Binary = original })

	_, err := Query(context.Background())
	if !errors.Is(err, ErrUnanswered) {
		t.Errorf("a missing CLI must be unanswered, got %v", err)
	}
}

func TestDeclineForwardsNever(t *testing.T) {
	dir := t.TempDir()
	argsFile := filepath.Join(dir, "args")
	path := filepath.Join(dir, "gaia")
	script := "#!/bin/sh\necho \"$@\" > " + argsFile + "\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("stub: %v", err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })

	if err := Decline(context.Background(), true); err != nil {
		t.Fatalf("Decline: %v", err)
	}
	recorded, err := os.ReadFile(argsFile)
	if err != nil {
		t.Fatalf("reading the recorded args: %v", err)
	}
	if !strings.Contains(string(recorded), "--never") {
		t.Errorf("--never did not reach the CLI, got %q", recorded)
	}
}

func TestDeclineOmitsNeverForAPlainSkip(t *testing.T) {
	dir := t.TempDir()
	argsFile := filepath.Join(dir, "args")
	path := filepath.Join(dir, "gaia")
	if err := os.WriteFile(path,
		[]byte("#!/bin/sh\necho \"$@\" > "+argsFile+"\n"), 0o755); err != nil {
		t.Fatalf("stub: %v", err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })

	if err := Decline(context.Background(), false); err != nil {
		t.Fatalf("Decline: %v", err)
	}
	recorded, _ := os.ReadFile(argsFile)
	if strings.Contains(string(recorded), "--never") {
		t.Errorf("a plain skip must not be permanent, got %q", recorded)
	}
}

func TestSummaryDistinguishesEveryState(t *testing.T) {
	cases := []struct {
		name   string
		status Status
		want   string
	}{
		{"running", Status{Running: true, Configured: true, TeamName: "Acme"}, "running"},
		{"configured but stopped", Status{Configured: true, TeamName: "Acme"}, "not running"},
		{"installed only", Status{SlackInstalled: true}, "not connected"},
		{"absent", Status{}, "no Slack app"},
	}
	seen := map[string]bool{}
	for _, tc := range cases {
		got := tc.status.Summary()
		if !strings.Contains(got, tc.want) {
			t.Errorf("%s: Summary() = %q, want it to mention %q", tc.name, got, tc.want)
		}
		if seen[got] {
			t.Errorf("%s: Summary() %q collides with another state", tc.name, got)
		}
		seen[got] = true
	}
}

func TestSummaryNamesTheWorkspaceWhenKnown(t *testing.T) {
	got := Status{Configured: true, TeamName: "Acme"}.Summary()
	if !strings.Contains(got, "Acme") {
		t.Errorf("Summary() = %q, want the workspace named", got)
	}
}

func TestSetupCommandAsksForSetup(t *testing.T) {
	original := Binary
	Binary = func() (string, error) { return "/usr/bin/gaia", nil }
	t.Cleanup(func() { Binary = original })

	bin, args, err := SetupCommand()
	if err != nil {
		t.Fatalf("SetupCommand: %v", err)
	}
	if bin != "/usr/bin/gaia" {
		t.Errorf("bin = %q", bin)
	}
	if strings.Join(args, " ") != "slack setup" {
		t.Errorf("args = %v", args)
	}
}
