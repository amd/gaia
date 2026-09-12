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

func TestConnectSendsTheTokensOnStdinNotArgv(t *testing.T) {
	// A token in argv is visible to every other process through `ps` and lands
	// in shell history. This is the test that keeps it out.
	dir := t.TempDir()
	argsFile := filepath.Join(dir, "args")
	stdinFile := filepath.Join(dir, "stdin")
	path := filepath.Join(dir, "gaia")
	script := "#!/bin/sh\n" +
		"echo \"$@\" > " + argsFile + "\n" +
		"cat > " + stdinFile + "\n" +
		"echo '{\"connected\": true, \"team\": \"Acme\"}'\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("stub: %v", err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })

	team, err := Connect(context.Background(), "xapp-secret", "xoxb-secret")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	if team != "Acme" {
		t.Errorf("team = %q, want Acme", team)
	}

	argv, _ := os.ReadFile(argsFile)
	if strings.Contains(string(argv), "secret") {
		t.Errorf("a token reached argv: %q", argv)
	}
	stdin, _ := os.ReadFile(stdinFile)
	if string(stdin) != "xapp-secret\nxoxb-secret\n" {
		t.Errorf("stdin = %q, want both tokens one per line", stdin)
	}
}

func TestConnectSurfacesTheCLIsOwnRefusal(t *testing.T) {
	// The CLI explains a swapped pair far better than an exit code does.
	stubGaia(t, "\xe2\x9d\x8c The bot token must start with 'xoxb-'.", 2)

	_, err := Connect(context.Background(), "xoxb-swapped", "xapp-swapped")
	if err == nil {
		t.Fatal("a refused pair must not look like success")
	}
	if !strings.Contains(err.Error(), "xoxb-") {
		t.Errorf("the CLI's own words must reach the user, got: %v", err)
	}
}

func TestConnectRejectsOutputThatIsNotAConfirmation(t *testing.T) {
	stubGaia(t, "something else entirely", 0)

	if _, err := Connect(context.Background(), "xapp-a", "xoxb-b"); err == nil {
		t.Error("exit 0 alone must not be read as connected")
	}
}

func TestCreateAppURLComesFromTheCLINotFromGo(t *testing.T) {
	// The manifest is a security surface — scopes, Socket Mode, which events
	// are subscribed. Two copies would drift.
	stubGaia(t, "https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D", 0)

	url, err := CreateAppURL(context.Background())
	if err != nil {
		t.Fatalf("CreateAppURL: %v", err)
	}
	if !strings.HasPrefix(url, "https://api.slack.com/apps") {
		t.Errorf("url = %q", url)
	}
}

func TestANonURLFromTheCLIIsRefused(t *testing.T) {
	stubGaia(t, "Traceback (most recent call last):", 0)

	if _, err := CreateAppURL(context.Background()); err == nil {
		t.Error("a traceback must not be handed to the user as a URL")
	}
}
