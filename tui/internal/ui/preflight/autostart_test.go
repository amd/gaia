package preflight

import (
	"context"
	"errors"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/ui/status"
)

// --- what may be started at all ---------------------------------------------

func TestCanAutoStart(t *testing.T) {
	cases := []struct {
		name string
		l    launcher
		want bool
	}{
		{
			name: "installed with an argv",
			l:    launcher{Found: true, Argv: []string{"/usr/local/bin/lemond"}},
			want: true,
		},
		{
			// The whole point of the scope line: starting is free, installing
			// pulls gigabytes and stays behind the `f` key.
			name: "nothing installed",
			l:    launcher{},
			want: false,
		},
		{
			// A display command with no argv is one only a human can drive (an
			// app bundle, a tray icon). Guessing an argv for it would run
			// something nobody resolved.
			name: "found but no spawnable form",
			l:    launcher{Found: true, Start: "open /Applications/Lemonade.app"},
			want: false,
		},
		{
			// LEMONADE_SERVER_PATH naming something absent. Starting anything
			// else would ignore a choice the user made explicitly.
			name: "bad override",
			l:    launcher{Found: true, Argv: []string{"x"}, BadOverride: "/nope/lemonade"},
			want: false,
		},
		{
			// Foreground describes what the command does to a HUMAN's terminal.
			// Spawned detached there is no terminal to occupy.
			name: "foreground daemon is still startable",
			l:    launcher{Found: true, Argv: []string{"/usr/local/bin/lemond"}, Foreground: true},
			want: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := canAutoStart(tc.l); got != tc.want {
				t.Fatalf("canAutoStart(%+v) = %v, want %v", tc.l, got, tc.want)
			}
		})
	}
}

func TestStartLemonadeRefusesWhatItMayNotStart(t *testing.T) {
	err := startLemonade(context.Background(), launcher{}, func(context.Context) bool { return true })
	if !errors.Is(err, errNoAutoStart) {
		t.Fatalf("want errNoAutoStart for an empty launcher, got %v", err)
	}
}

// --- the context window reaches the child -----------------------------------

func TestStartEnvCarriesTheContextWindow(t *testing.T) {
	env := startEnv(launcher{CtxSize: 65536})
	if !hasEnv(env, ctxSizeEnv+"=65536") {
		t.Fatalf("%s missing from the child env; a server that comes up at the "+
			"wrong window answers /health and 502s every query", ctxSizeEnv)
	}
}

func TestServiceManagedLauncherGetsNoContextWindow(t *testing.T) {
	// The unit file or plist owns it. Setting it on the systemctl CLIENT
	// process changes nothing while looking like it did.
	env := startEnv(launcher{CtxSize: 65536, ServiceManaged: true})
	if hasEnv(env, ctxSizeEnv+"=65536") {
		t.Fatalf("%s must not be set for a service-managed launcher", ctxSizeEnv)
	}
}

func hasEnv(env []string, want string) bool {
	for _, e := range env {
		if e == want {
			return true
		}
	}
	return false
}

// --- waiting --------------------------------------------------------------

func TestWaitForLemonadeReturnsAsSoonAsItAnswers(t *testing.T) {
	calls := 0
	start := time.Now()
	ok := waitForLemonade(context.Background(), func(context.Context) bool {
		calls++
		return calls >= 2
	})
	if !ok {
		t.Fatal("want true once the probe succeeds")
	}
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Fatalf("took %v; it should return on the first success, not wait out the window", elapsed)
	}
}

func TestWaitForLemonadeGivesUpWhenCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if waitForLemonade(ctx, func(context.Context) bool { return false }) {
		t.Fatal("a cancelled context must not report the server up")
	}
}

// --- the row ---------------------------------------------------------------

// stubProbe makes the row tests independent of whether this machine has a
// Lemonade running. Without it, a developer box with one up short-circuits
// checkLemonade before auto-start is ever reached — the tests pass having
// exercised none of it.
func stubProbe(t *testing.T, reachable bool) {
	t.Helper()
	restore := probeLemonade
	t.Cleanup(func() { probeLemonade = restore })
	probeLemonade = func(context.Context) (string, bool, string) {
		if reachable {
			return "http://localhost:13305/api/v1", true, "stub: up"
		}
		return "", false, "stub: nothing answering"
	}
}

func TestLemonadeRowReportsAStartItPerformed(t *testing.T) {
	stubProbe(t, false)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = func(context.Context) (bool, string, string) {
		return true, "http://localhost:13305/api/v1", "auto-start: ran lemond"
	}

	row := localRunner{opts: LocalOptions{}}.checkLemonade(context.Background(), Config{})
	if row.State != StateOK {
		t.Fatalf("row state = %v, want StateOK after a successful start", row.State)
	}
	if !strings.Contains(row.Line, "started for you") {
		t.Fatalf("row line %q should say the server was started, not merely that "+
			"it is running — the user did not start it", row.Line)
	}
}

func TestLemonadeRowStillHaltsWhenTheStartFails(t *testing.T) {
	stubProbe(t, false)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = func(context.Context) (bool, string, string) {
		return false, "", "auto-start: could not run lemond: permission denied"
	}

	row := localRunner{opts: LocalOptions{}}.checkLemonade(context.Background(), Config{})
	if row.State != StateFailed || row.Disposition != status.DispositionHalt {
		t.Fatalf("a failed start must leave the row red and halting, got state=%v disposition=%v",
			row.State, row.Disposition)
	}
	if !strings.Contains(row.Raw, "permission denied") {
		t.Fatalf("the failure must reach the details pane, got Raw=%q", row.Raw)
	}
}

func TestLemonadeRowSaysNothingAboutStartingWhenItDidNotTry(t *testing.T) {
	stubProbe(t, false)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = func(context.Context) (bool, string, string) {
		return false, "", "" // nothing installed
	}

	row := localRunner{opts: LocalOptions{}}.checkLemonade(context.Background(), Config{})
	if strings.Contains(row.Raw, "auto-start") {
		t.Fatalf("no attempt was made, so the details must not mention one: %q", row.Raw)
	}
	if row.Fix != FixRunSetup {
		t.Fatalf("an uninstalled Lemonade must keep its one-key setup, got Fix=%v", row.Fix)
	}
}

func TestLemonadeRowDoesNotStartASecondServer(t *testing.T) {
	stubProbe(t, true)
	restore := tryAutoStartLemonade
	t.Cleanup(func() { tryAutoStartLemonade = restore })
	tryAutoStartLemonade = func(context.Context) (bool, string, string) {
		t.Fatal("a reachable Lemonade must short-circuit before any start attempt")
		return false, "", ""
	}

	row := localRunner{opts: LocalOptions{}}.checkLemonade(context.Background(), Config{})
	if row.State != StateOK {
		t.Fatalf("row state = %v, want StateOK for a server already up", row.State)
	}
}

// --- every resolver branch carries a spawnable form -------------------------

func TestResolvedLaunchersCarryAnArgv(t *testing.T) {
	cases := []struct {
		name string
		p    hostProbe
	}{
		{"windows", winProbeWithServer()},
		{"linux systemd", linuxProbeWithUnit()},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := resolveLauncherWith(tc.p)
			if !l.Found {
				t.Skipf("probe did not find a launcher; covered by lemonade_test.go")
			}
			if len(l.Argv) == 0 {
				t.Fatalf("%s resolved Start=%q but no Argv, so the TUI can only "+
					"print the fix and never apply it", tc.name, l.Start)
			}
			if strings.ContainsAny(l.Argv[0], `"`) {
				t.Fatalf("Argv[0]=%q is quoted; argv entries are passed to exec "+
					"directly and must not carry shell quoting", l.Argv[0])
			}
		})
	}
}

func winProbeWithServer() hostProbe {
	exe := `C:\Users\x\AppData\Local\lemonade_server\bin\LemonadeServer.exe`
	return hostProbe{
		goos: "windows",
		getenv: func(k string) string {
			if k == "LOCALAPPDATA" {
				return `C:\Users\x\AppData\Local`
			}
			return ""
		},
		exists:   func(p string) bool { return p == exe },
		lookPath: func(string) (string, error) { return "", os.ErrNotExist },
		homeDir:  func() (string, error) { return `C:\Users\x`, nil },
		readFile: func(string) ([]byte, error) { return nil, os.ErrNotExist },
	}
}

func linuxProbeWithUnit() hostProbe {
	return hostProbe{
		goos:   "linux",
		getenv: func(string) string { return "" },
		exists: func(p string) bool {
			return p == "/usr/bin/lemond" || strings.HasSuffix(p, ".service")
		},
		lookPath: func(name string) (string, error) {
			if name == "systemctl" {
				return "/usr/bin/systemctl", nil
			}
			return "", os.ErrNotExist
		},
		homeDir:  func() (string, error) { return "/home/x", nil },
		readFile: func(string) ([]byte, error) { return nil, os.ErrNotExist },
	}
}

func TestMain(m *testing.M) {
	if runtime.GOOS == "" { // keeps the import used on every platform
		return
	}
	os.Exit(m.Run())
}
