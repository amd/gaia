package preflight

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// THE regression. `lemonade-server serve` was hardcoded into the Local AI row,
// and that CLI does not exist on a modern Lemonade install (10.7+ dropped it) —
// so the row correctly detected the outage and then handed the user a command
// that errors, which is the one failure mode worse than saying nothing: no path
// forward, and no reason to doubt the instruction.
//
// This asserts the property, not a string: whatever the row names has to be a
// program THIS machine can run. A literal expectation is what let the stale one
// sit here looking asserted-as-real.
func TestTheLocalAIRemedyNamesACommandThisMachineHas(t *testing.T) {
	rows := map[string]*fakeTransport{
		"not reachable":         newFake().with("GET /v1/email/init", 503, initUnreachable),
		"model list unreadable": newFake().with("GET /v1/email/init", 503, initModelListUnreadable),
	}
	for name, f := range rows {
		t.Run(name, func(t *testing.T) {
			rep := Check(context.Background(), f, EmailConfig())
			row, ok := rep.Find(KeyLemonade)
			if !ok || row.State != StateFailed {
				t.Fatalf("expected a failed Local AI row:\n%s", rep)
			}
			assertRunnable(t, row.Remedy.Command)

			// And the specific stale command, unless this machine really has it.
			if _, err := exec.LookPath("lemonade-server"); err != nil {
				if strings.Contains(row.Remedy.Command, "lemonade-server serve") {
					t.Errorf("the row names `lemonade-server serve` on a machine without it: %q",
						row.Remedy.Command)
				}
			}
		})
	}
}

// The same property through the ladder, which the chat view shares — a mid-run
// Lemonade outage must not print a command the gate would not.
func TestTheLadderNamesACommandThisMachineHas(t *testing.T) {
	l := Ladder{AgentID: "email"}
	for name, d := range map[string]Diagnosis{
		"not reachable": l.Text("check the local AI",
			"Local Lemonade Server is not reachable at http://localhost:13305/api/v1"),
		"timed out": l.Text("check the local AI", "the request timed out"),
	} {
		t.Run(name, func(t *testing.T) {
			assertRunnable(t, d.Command)
		})
	}
}

// --- the platform matrix ----------------------------------------------------

// fakeHostFor builds a probe for a machine this test is not running on, so the
// Windows and Linux answers are provable from anywhere.
// fakeHostFor builds a probe for a machine this test is not running on. With no
// ~/.gaia/config.json it resolves the GPU window, matching GaiaConfig's own
// default; fakeHostWithDevice covers the NPU profile.
func fakeHostFor(goos string, onPath []string, files []string, env map[string]string) hostProbe {
	return fakeHostWithDevice(goos, onPath, files, env, "")
}

func fakeHostWithDevice(goos string, onPath, files []string, env map[string]string, device string) hostProbe {
	has := func(list []string, want string) bool {
		for _, s := range list {
			if s == want {
				return true
			}
		}
		return false
	}
	return hostProbe{
		goos: goos,
		// A distinctive directory on purpose: the legacy branch deliberately emits
		// the BARE name (it is on PATH, and the short form is what a user reads),
		// while lemonade_launcher.py keeps the resolved path. Returning
		// /usr/local/bin here would make those two indistinguishable.
		lookPath: func(name string) (string, error) {
			if has(onPath, name) {
				return "/fake/bin/" + name, nil
			}
			return "", errors.New("not found")
		},
		exists:  func(path string) bool { return has(files, path) },
		getenv:  func(key string) string { return env[key] },
		homeDir: func() (string, error) { return "/home/jane", nil },
		readFile: func(string) ([]byte, error) {
			if device == "" {
				return nil, errors.New("no config")
			}
			return []byte(`{"profile":"x","default_device":"` + device + `"}`), nil
		},
	}
}

// Each platform's launcher, against the tooling that platform actually ships.
// Every expectation here is the command from the docs or from
// gaia.llm.lemonade_launcher.build_start_command — never `lemonade-server serve`.
func TestEveryPlatformGetsALauncherThatExistsThere(t *testing.T) {
	cases := []struct {
		name        string
		probe       hostProbe
		wantStart   string
		wantRestart string
		wantHint    string
	}{
		{
			name: "modern Linux runs the daemon under a systemd user unit",
			probe: fakeHostFor("linux", []string{"systemctl"},
				[]string{"/usr/bin/lemonade", "/usr/bin/lemond",
					"/usr/lib/systemd/user/lemond.service"}, nil),
			// build_start_command's modern-Linux branch, and restart is a REAL
			// distinct command there — the one a wedged daemon needs.
			wantStart:   "systemctl --user start lemond",
			wantRestart: "systemctl --user restart lemond",
		},
		{
			// The unit and systemctl are BOTH required. A container, WSL1, a
			// non-systemd distro or a tarball install answers systemctl with an
			// error — which is a phantom command in the original shape.
			name: "Linux without systemd names the daemon binary instead",
			probe: fakeHostFor("linux", nil,
				[]string{"/usr/bin/lemonade", "/usr/bin/lemond"}, nil),
			wantStart: "env LEMONADE_CTX_SIZE=65536 /usr/bin/lemond",
		},
		{
			name: "Linux with systemctl but no unit file names the binary too",
			probe: fakeHostFor("linux", []string{"systemctl"},
				[]string{"/usr/bin/lemond"}, nil),
			wantStart: "env LEMONADE_CTX_SIZE=65536 /usr/bin/lemond",
		},
		{
			// No PATH entry at all: a TUI launched from a GUI session inherits
			// /usr/bin:/bin:/usr/sbin:/sbin, and a PATH-only probe would report this
			// fully installed machine as having nothing — then advise `gaia init`
			// over a server that is merely stopped.
			name:      "macOS resolves lemond by absolute path, not PATH",
			probe:     fakeHostFor("darwin", nil, []string{"/usr/local/bin/lemond"}, nil),
			wantStart: "env LEMONADE_CTX_SIZE=65536 /usr/local/bin/lemond",
			wantHint:  "Applications folder",
		},
		{
			// Verified over a guess: the binary is what was actually observed to
			// bring the port up, so it wins over both the launchd job (unverifiable
			// sudo) and the app bundle (starts the tray, not the server).
			name: "macOS prefers the verified binary over the launchd job",
			probe: fakeHostFor("darwin", []string{"launchctl"},
				[]string{macDaemonPlist, "/usr/local/bin/lemond",
					"/Applications/lemonade-app.app"}, nil),
			wantStart: "env LEMONADE_CTX_SIZE=65536 /usr/local/bin/lemond",
			wantHint:  "Applications folder",
		},
		{
			// …but a machine that has ONLY the job still gets it, rather than being
			// told nothing is installed when the plist is plainly there.
			name: "macOS falls back to launchd when nothing else is there",
			probe: fakeHostFor("darwin", []string{"launchctl"},
				[]string{macDaemonPlist}, nil),
			wantStart:   "sudo launchctl kickstart system/com.lemonade.server",
			wantRestart: "sudo launchctl kickstart -k system/com.lemonade.server",
			wantHint:    "Applications folder",
		},
		{
			name: "modern Windows runs the server binary from LOCALAPPDATA",
			probe: fakeHostFor("windows", nil,
				[]string{filepath.Join(`C:\Users\Jane Doe\AppData\Local`, "lemonade_server", "bin", "LemonadeServer.exe")},
				map[string]string{"LOCALAPPDATA": `C:\Users\Jane Doe\AppData\Local`}),
			// Quoted: the canonical path sits under the user profile, which can
			// contain a space, and an unquoted one is not copy-pasteable.
			wantStart: `set "LEMONADE_CTX_SIZE=65536" && ` + `"` + filepath.Join(`C:\Users\Jane Doe\AppData\Local`, "lemonade_server", "bin", "LemonadeServer.exe") + `" --silent`,
			wantHint:  "tray icon",
		},
		{
			// The name the current installer actually uses — verified on a live
			// 10.10.0 box. Guessed names stay existence-gated, so they cannot
			// produce a command for a bundle that is not there.
			name: "macOS with only the app bundle opens the app",
			probe: fakeHostFor("darwin", nil,
				[]string{"/Applications/lemonade-app.app"}, nil),
			wantStart: "open /Applications/lemonade-app.app",
			wantHint:  "Applications folder",
		},
		{
			name:      "a legacy machine still gets the legacy CLI",
			probe:     fakeHostFor("linux", []string{"lemonade-server"}, nil, nil),
			wantStart: "lemonade-server serve --ctx-size 65536",
		},
		{
			name:      "the pip/CI variant counts as legacy too",
			probe:     fakeHostFor("linux", []string{"lemonade-server-dev"}, nil, nil),
			wantStart: "lemonade-server-dev serve --ctx-size 65536",
		},
		{
			name: "an explicit override is run verbatim, never rerouted",
			probe: fakeHostFor("linux", []string{"systemctl"},
				[]string{"/usr/bin/lemonade", "/usr/lib/systemd/user/lemond.service",
					"/opt/mine/lemonade-server"},
				map[string]string{serverPathEnv: "/opt/mine/lemonade-server"}),
			wantStart: "env LEMONADE_CTX_SIZE=65536 /opt/mine/lemonade-server",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := resolveLemonadeWith(tc.probe)
			if !got.Found {
				t.Fatalf("resolved nothing: %+v", got)
			}
			if got.Start != tc.wantStart {
				t.Errorf("start = %q, want %q", got.Start, tc.wantStart)
			}
			wantRestart := tc.wantRestart
			if wantRestart == "" {
				wantRestart = tc.wantStart
			}
			if got.Restart != wantRestart {
				t.Errorf("restart = %q, want %q", got.Restart, wantRestart)
			}
			if tc.wantHint != "" && !strings.Contains(got.AppHint, tc.wantHint) {
				t.Errorf("app hint = %q, want it to mention %q", got.AppHint, tc.wantHint)
			}
		})
	}
}

// An override that names the LEGACY cli must not be silently rerouted to
// systemctl just because the machine also has the modern package — the whole
// point of the variable is "run this one".
func TestAnOverrideBeatsTheModernPackageOnTheSameMachine(t *testing.T) {
	probe := fakeHostFor("linux", []string{"lemonade-server", "systemctl"},
		[]string{"/usr/bin/lemonade", "/usr/bin/lemond",
			"/usr/lib/systemd/user/lemond.service", "/opt/lemonade/bin/lemond"},
		map[string]string{serverPathEnv: "/opt/lemonade/bin/lemond"})

	got := resolveLemonadeWith(probe)
	if got.Start != "env LEMONADE_CTX_SIZE=65536 /opt/lemonade/bin/lemond" {
		t.Errorf("start = %q, want the override run verbatim", got.Start)
	}
	if strings.Contains(got.Start, "systemctl") {
		t.Error("an explicit override was rerouted to systemctl")
	}
}

// A path with a space has to survive into a command the user can paste.
func TestAnOverridePathWithASpaceIsQuoted(t *testing.T) {
	probe := fakeHostFor("darwin", nil, []string{"/Users/jane/My Tools/lemond"},
		map[string]string{serverPathEnv: "/Users/jane/My Tools/lemond"})
	if got := resolveLemonadeWith(probe).Start; got != `env LEMONADE_CTX_SIZE=65536 "/Users/jane/My Tools/lemond"` {
		t.Errorf("start = %q, want it quoted", got)
	}
}

// Nothing installed is NOT "start it": you cannot start what is not there, and
// naming a start command for it is the same unactionable-remedy bug in a
// different shape. The installer is the honest answer.
func TestNothingInstalledSendsTheUserToTheInstaller(t *testing.T) {
	probe := fakeHostFor("darwin", nil, nil, nil)
	got := resolveLemonadeWith(probe)
	if got.Found {
		t.Fatalf("resolved a launcher on a machine with no Lemonade: %+v", got)
	}
	if got.Start != "" {
		t.Errorf("start = %q, want empty when nothing is installed", got.Start)
	}

	// Which the remedies turn into `gaia init`, not a phantom server command.
	start := withProbe(probe, lemonadeStartRemedy)
	if start.Command != "gaia init" {
		t.Errorf("start remedy = %q, want `gaia init`", start.Command)
	}
	if !strings.Contains(start.Action, "not on this machine") {
		t.Errorf("the remedy does not say it is missing: %q", start.Action)
	}
	if start.Where == "" {
		t.Error("a not-installed remedy does not say where to read next")
	}
}

// The restart remedy must not claim one command is enough where the launcher has
// no restart form — a user who runs `lemond` against an already-listening port
// gets a bind error and no restart.
func TestTheRestartRemedySaysToQuitFirstWhenThereIsNoRestartForm(t *testing.T) {
	systemd := withProbe(
		fakeHostFor("linux", []string{"systemctl"},
			[]string{"/usr/bin/lemond", "/usr/lib/systemd/user/lemond.service"}, nil),
		lemonadeRestartRemedy)
	if systemd.Command != "systemctl --user restart lemond" {
		t.Errorf("systemd restart = %q", systemd.Command)
	}
	if strings.Contains(systemd.Action, "Quit it") {
		t.Errorf("systemd has a restart form; the remedy should just use it: %q", systemd.Action)
	}

	bare := withProbe(
		fakeHostFor("darwin", nil, []string{"/usr/local/bin/lemond"}, nil), lemonadeRestartRemedy)
	if !strings.Contains(bare.Action, "Quit it") {
		t.Errorf("a launcher with no restart form must say to quit first: %q", bare.Action)
	}
}

// A server that answers the port while GAIA cannot name its launcher must not be
// told it is "not installed" — something is plainly running.
func TestARunningButUnresolvableServerIsNotCalledMissing(t *testing.T) {
	got := withProbe(fakeHostFor("darwin", nil, nil, nil), lemonadeRestartRemedy)
	if strings.Contains(got.Action, "not on this machine") {
		t.Errorf("a responding server was reported as absent: %q", got.Action)
	}
	if !strings.Contains(got.Action, "could not work out how") {
		t.Errorf("the remedy does not admit what it does not know: %q", got.Action)
	}
	// And the command has to be one that actually clears a running server. `gaia
	// init` skips the start when it finds the port answering, so it would not.
	if got.Command == "gaia init" {
		t.Error("the restart remedy names a command that will not restart a reachable server")
	}
}

// withProbe runs one of the remedy builders against a fake machine. The builders
// read the host through resolveLemonade, so the swap has to happen there.
func withProbe(p hostProbe, build func() Remedy) Remedy {
	saved := realHostProbe
	realHostProbe = func() hostProbe { return p }
	defer func() { realHostProbe = saved }()
	return build()
}

// The resolver is called fresh on every check rather than memoised: the gate
// caches nothing on purpose, and a user who installs Lemonade and presses r has
// to be told the command they now have — not the one they had at startup.
func TestTheLauncherIsNotCachedAcrossChecks(t *testing.T) {
	missing := fakeHostFor("darwin", nil, nil, nil)
	installed := fakeHostFor("darwin", nil, []string{"/usr/local/bin/lemond"}, nil)

	if withProbe(missing, lemonadeStartRemedy).Command != "gaia init" {
		t.Fatal("baseline: a machine with no Lemonade should be sent to the installer")
	}
	if got := withProbe(installed, lemonadeStartRemedy).Command; got != "env LEMONADE_CTX_SIZE=65536 /usr/local/bin/lemond" {
		t.Errorf("after an install the remedy is still %q — the resolver was cached", got)
	}
}

// Sanity on the real host, so this file is not only ever exercised against fakes.
func TestTheRealHostResolvesToSomethingCoherent(t *testing.T) {
	l := resolveLemonade()
	t.Logf("%s: found=%v start=%q restart=%q hint=%q",
		runtime.GOOS, l.Found, l.Start, l.Restart, l.AppHint)

	if !l.Found {
		if l.Start != "" || l.Restart != "" {
			t.Errorf("not found, yet it produced commands: %+v", l)
		}
		return
	}
	assertRunnable(t, l.Start)
	assertRunnable(t, l.Restart)
	if strings.Contains(l.Start, "<") {
		t.Errorf("the start command has a placeholder in it: %q", l.Start)
	}
}

// A LEMONADE_SERVER_PATH pointing at nothing is its own state. The Python runs it
// and lets exec fail loudly; a row ADVISES a human, so printing a `run:` line for
// a file that is not there is the exact bug this file removes — and silently
// falling through to a different launcher would hide that the variable is wrong.
func TestAStaleOverrideIsReportedRatherThanObeyedOrIgnored(t *testing.T) {
	probe := fakeHostFor("darwin", []string{"launchctl"},
		[]string{macDaemonPlist, "/usr/local/bin/lemond"},
		map[string]string{serverPathEnv: "/opt/gone/lemond"})

	l := resolveLemonadeWith(probe)
	if l.Found {
		t.Errorf("a stale override resolved as usable tooling: %+v", l)
	}
	if l.BadOverride != "/opt/gone/lemond" {
		t.Errorf("BadOverride = %q, want the path that is missing", l.BadOverride)
	}
	if l.Start != "" {
		t.Errorf("a stale override still produced a start command: %q", l.Start)
	}

	for name, r := range map[string]Remedy{
		"start":   withProbe(probe, lemonadeStartRemedy),
		"restart": withProbe(probe, lemonadeRestartRemedy),
	} {
		if !strings.Contains(r.Action, serverPathEnv) {
			t.Errorf("%s remedy does not name the variable at fault: %q", name, r.Action)
		}
		if !strings.Contains(r.Action, "/opt/gone/lemond") {
			t.Errorf("%s remedy does not say what the variable points at: %q", name, r.Action)
		}
		// It must NOT advise installing: the machine has Lemonade, the env is wrong.
		if r.Command == "gaia init" {
			t.Errorf("%s remedy sends the user to the installer over a bad env var", name)
		}
	}
}

// The property every assertion in this package now leans on: the remedy always
// names something. resolveLemonade().Start is "" on a machine with no Lemonade —
// every CI runner — and an empty expectation is either skipped by the table or
// trivially satisfied by strings.Contains, which turns the guard off exactly
// where it is most needed.
func TestTheRemedyAlwaysNamesACommandEvenWithNothingInstalled(t *testing.T) {
	probes := map[string]hostProbe{
		"nothing at all":     fakeHostFor("linux", nil, nil, nil),
		"nothing on windows": fakeHostFor("windows", nil, nil, nil),
		"nothing on macOS":   fakeHostFor("darwin", nil, nil, nil),
		"stale override":     fakeHostFor("linux", nil, nil, map[string]string{serverPathEnv: "/gone"}),
	}
	for name, probe := range probes {
		t.Run(name, func(t *testing.T) {
			if got := resolveLemonadeWith(probe); got.Start != "" {
				t.Fatalf("expected no launcher, got %q", got.Start)
			}
			for kind, build := range map[string]func() Remedy{
				"start":   lemonadeStartRemedy,
				"restart": lemonadeRestartRemedy,
			} {
				r := withProbe(probe, build)
				if r.Command == "" {
					t.Errorf("%s remedy has no command, so every assertion built on it "+
						"silently stops asserting", kind)
				}
				if r.Action == "" || r.Where == "" {
					t.Errorf("%s remedy is not actionable: %+v", kind, r)
				}
			}
		})
	}
}

// The macOS ordering, pinned with its reason. Two more obvious orders are both
// wrong on a real 10.10.0 machine: opening the app bundle produced only
// `lemonade-tray` and no server, and the launchd job reported `state = not
// running` while an out-of-launchd lemond served the port — and driving that job
// needs sudo, so it could never be verified. The binary was verified end to end.
func TestMacOSPrefersWhatWasActuallyVerifiedToWork(t *testing.T) {
	everything := fakeHostFor("darwin", []string{"launchctl"},
		[]string{macDaemonPlist, "/Applications/lemonade-app.app", "/usr/local/bin/lemond"}, nil)

	l := resolveLemonadeWith(everything)
	if !strings.HasSuffix(l.Start, "/usr/local/bin/lemond") {
		t.Errorf("start = %q, want the binary that was verified to serve", l.Start)
	}
	if strings.Contains(l.Start, "sudo") {
		t.Error("the row asks for sudo where a no-privilege command was verified to work")
	}
	if !l.Foreground {
		t.Error("running the binary holds the terminal; the remedy has to know that")
	}
	// And the remedy says so, instead of "then press r" in a shell that never
	// comes back.
	r := withProbe(everything, lemonadeStartRemedy)
	if !strings.Contains(r.Action, "keeps that terminal") {
		t.Errorf("the remedy implies the shell returns: %q", r.Action)
	}
	if !strings.Contains(r.Action, "Applications folder") {
		t.Errorf("the no-terminal alternative is not offered: %q", r.Action)
	}
}

// --- the context window -----------------------------------------------------

// A bare `lemond` comes up HEALTHY — /api/v1/health answers 200 — and then 502s
// every agent query, because the agent asks for a context the server was never
// started with. A remedy that produces that is worse than a dead command: the
// gate's own Local AI row goes green off the healthy server and hands the user
// into a chat where the first real request fails. Verified on a live 10.10.0
// machine and recorded in CLAUDE.md.
func TestAStartCommandCarriesTheContextWindow(t *testing.T) {
	cases := map[string]struct {
		probe hostProbe
		want  string
	}{
		"macOS binary":  {fakeHostFor("darwin", nil, []string{"/usr/local/bin/lemond"}, nil), "LEMONADE_CTX_SIZE=65536"},
		"Linux binary":  {fakeHostFor("linux", nil, []string{"/usr/bin/lemond"}, nil), "LEMONADE_CTX_SIZE=65536"},
		"Windows exe":   {windowsProbe(), "LEMONADE_CTX_SIZE=65536"},
		"legacy CLI":    {fakeHostFor("linux", []string{"lemonade-server"}, nil, nil), "--ctx-size 65536"},
		"path override": {overrideProbe(), "LEMONADE_CTX_SIZE=65536"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			l := resolveLemonadeWith(tc.probe)
			if !strings.Contains(l.Start, tc.want) {
				t.Errorf("start = %q, want it to carry %q", l.Start, tc.want)
			}
			if !strings.Contains(l.Restart, tc.want) {
				t.Errorf("restart = %q, want it to carry %q", l.Restart, tc.want)
			}
		})
	}
}

func windowsProbe() hostProbe {
	exe := filepath.Join(`C:\Users\jane\AppData\Local`, "lemonade_server", "bin", "LemonadeServer.exe")
	return fakeHostFor("windows", nil, []string{exe},
		map[string]string{"LOCALAPPDATA": `C:\Users\jane\AppData\Local`})
}

func overrideProbe() hostProbe {
	return fakeHostFor("linux", nil, []string{"/opt/x/lemond"},
		map[string]string{serverPathEnv: "/opt/x/lemond"})
}

// A launch whose environment belongs to a unit file, a plist, or an app bundle
// cannot carry the window on the command line — prefixing one would look like it
// did something and change nothing. Those say where it comes from instead.
func TestAServiceLaunchNamesTheWindowRatherThanFakingAPrefix(t *testing.T) {
	cases := map[string]hostProbe{
		"systemd unit": fakeHostFor("linux", []string{"systemctl"},
			[]string{"/usr/bin/lemond", "/usr/lib/systemd/user/lemond.service"}, nil),
		"macOS app bundle": fakeHostFor("darwin", nil, []string{"/Applications/lemonade-app.app"}, nil),
		"launchd job": fakeHostFor("darwin", []string{"launchctl"},
			[]string{macDaemonPlist}, nil),
	}
	for name, probe := range cases {
		t.Run(name, func(t *testing.T) {
			l := resolveLemonadeWith(probe)
			if strings.Contains(l.Start, "env "+ctxSizeEnv) || strings.Contains(l.Start, `set "`+ctxSizeEnv) {
				t.Errorf("an inert env prefix was attached to a service launch: %q", l.Start)
			}
			if l.CtxSize != gpuCtxSize {
				t.Errorf("ctx = %d, want it resolved even when the command cannot carry it", l.CtxSize)
			}
			// …and the remedy says so, or the user reads the 502 as an agent bug.
			r := withProbe(probe, lemonadeStartRemedy)
			if !strings.Contains(r.Action, ctxSizeEnv) {
				t.Errorf("the remedy never mentions the window: %q", r.Action)
			}
			if !strings.Contains(r.Action, "looks healthy") {
				t.Errorf("the remedy does not warn that the failure looks like health: %q", r.Action)
			}
		})
	}
}

// The window is DERIVED from the machine's recorded device profile, never
// hardcoded: the NPU's FastFlowLM build is registered at 32768 and cannot reach
// 65536, so handing it the GPU window fails the load outright.
func TestTheWindowComesFromTheRecordedDeviceProfile(t *testing.T) {
	for device, want := range map[string]int{
		"npu":      npuCtxSize,
		"NPU":      npuCtxSize, // case-insensitive, like profile_ctx_size
		"gpu":      gpuCtxSize,
		"cpu":      gpuCtxSize,
		"":         gpuCtxSize, // no config at all — GaiaConfig's own default
		"nonsense": gpuCtxSize,
	} {
		probe := fakeHostWithDevice("darwin", nil, []string{"/usr/local/bin/lemond"}, nil, device)
		if got := resolveLemonadeWith(probe).CtxSize; got != want {
			t.Errorf("default_device=%q resolved to %d, want %d", device, got, want)
		}
	}
}

// These MUST equal lemonade_client.GPU_CTX_SIZE / NPU_CTX_SIZE. Collapsing them
// to one number would cap GPU doc-Q&A at 32K and re-open the #1030 overflow;
// swapping them would fail the NPU load outright.
func TestTheWindowConstantsMatchTheirPythonSource(t *testing.T) {
	if gpuCtxSize != 65536 {
		t.Errorf("gpuCtxSize = %d, want lemonade_client.GPU_CTX_SIZE (65536)", gpuCtxSize)
	}
	if npuCtxSize != 32768 {
		t.Errorf("npuCtxSize = %d, want lemonade_client.NPU_CTX_SIZE (32768)", npuCtxSize)
	}
}

// A user who already set the variable has chosen; telling them a different
// number would contradict their own environment.
func TestAnExplicitWindowOverrideIsHonoured(t *testing.T) {
	probe := fakeHostWithDevice("darwin", nil, []string{"/usr/local/bin/lemond"},
		map[string]string{ctxSizeEnv: "16384"}, "npu")
	if got := resolveLemonadeWith(probe).CtxSize; got != 16384 {
		t.Errorf("ctx = %d, want the caller's own 16384", got)
	}
	// Garbage falls back to the profile rather than propagating into a command.
	bad := fakeHostWithDevice("darwin", nil, []string{"/usr/local/bin/lemond"},
		map[string]string{ctxSizeEnv: "not-a-number"}, "npu")
	if got := resolveLemonadeWith(bad).CtxSize; got != npuCtxSize {
		t.Errorf("ctx = %d, want the npu profile window", got)
	}
}

// The config path contract mirrors gaia.config exactly, or the TUI reads a
// different file than `gaia init` writes and silently disagrees with it.
func TestTheConfigPathMirrorsGaiaConfig(t *testing.T) {
	base := fakeHostWithDevice("darwin", nil, nil, nil, "")
	base.homeDir = func() (string, error) { return "/home/jane", nil }

	if got := configPath(base); got != filepath.Join("/home/jane", ".gaia", "config.json") {
		t.Errorf("default path = %q", got)
	}
	withDir := base
	withDir.getenv = func(k string) string { return map[string]string{configDirEnv: "/custom"}[k] }
	if got := configPath(withDir); got != filepath.Join("/custom", "config.json") {
		t.Errorf("GAIA_CONFIG_DIR path = %q", got)
	}
	withFile := base
	withFile.getenv = func(k string) string {
		return map[string]string{configDirEnv: "/custom", configFileEnv: "/exact/c.json"}[k]
	}
	if got := configPath(withFile); got != "/exact/c.json" {
		t.Errorf("GAIA_CONFIG_FILE must win over the dir, got %q", got)
	}
}

// The runnable check must step over the window prefix. Checking that `env`
// exists is always true and would stop checking the binary that has to be there.
func TestTheRunnableCheckLooksPastTheWindowPrefix(t *testing.T) {
	for cmd, want := range map[string]string{
		"env LEMONADE_CTX_SIZE=65536 /usr/local/bin/lemond":               "/usr/local/bin/lemond",
		`env LEMONADE_CTX_SIZE=65536 "/Users/j/My Tools/lemond"`:          "/Users/j/My Tools/lemond",
		`set "LEMONADE_CTX_SIZE=65536" && "C:\a b\Lemonade.exe" --silent`: `C:\a b\Lemonade.exe`,
		"lemonade-server serve --ctx-size 65536":                          "lemonade-server",
		"systemctl --user start lemond":                                   "systemctl",
		"/usr/local/bin/lemond":                                           "/usr/local/bin/lemond",
	} {
		if got := firstWord(cmd); got != want {
			t.Errorf("firstWord(%q) = %q, want %q", cmd, got, want)
		}
	}
}
