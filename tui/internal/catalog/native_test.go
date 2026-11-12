package catalog

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

// nativeFixture is a machine with nothing installed: an empty PATH, home, working
// directory and program directory. Each test then puts a file where it wants one
// found.
type nativeFixture struct {
	path, home, beside, cwd string
}

func isolateNative(t *testing.T) nativeFixture {
	t.Helper()
	f := nativeFixture{path: t.TempDir(), home: t.TempDir(), beside: t.TempDir(), cwd: t.TempDir()}
	t.Setenv("PATH", f.path)
	t.Setenv("HOME", f.home)
	t.Setenv("USERPROFILE", f.home)
	t.Chdir(f.cwd)
	orig := selfExecutable
	selfExecutable = func() (string, error) { return filepath.Join(f.beside, "gaia-tui"), nil }
	t.Cleanup(func() { selfExecutable = orig })
	return f
}

func writeProgram(t *testing.T, dir, name string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// selectAgent is SelectFlagshipAgent for a test that expects no notice.
func selectAgent(c *Catalog, choice string) error {
	notice, err := c.SelectFlagshipAgent(choice)
	if err == nil && notice != "" {
		return errors.New("unexpected notice: " + notice)
	}
	return err
}

func samePath(t *testing.T, got, want string) bool {
	t.Helper()
	g, err1 := filepath.EvalSymlinks(got)
	w, err2 := filepath.EvalSymlinks(want)
	if err1 != nil || err2 != nil {
		return strings.EqualFold(filepath.Clean(got), filepath.Clean(want))
	}
	return strings.EqualFold(g, w)
}

// An install that only has the Python agent must keep running it, launched
// exactly as before: no --stdio, which that program does not declare.
func TestWithoutGaiacppTheFlagshipStaysOnThePythonAgent(t *testing.T) {
	isolateNative(t)
	before := *NewCatalog().Get(FlagshipID)

	c := NewCatalog()
	if err := selectAgent(c, ""); err != nil {
		t.Fatalf("a machine without gaiacpp refused to pick an agent: %v", err)
	}
	if got := *c.Get(FlagshipID); !reflect.DeepEqual(got, before) {
		t.Errorf("the Python entry changed with no gaiacpp installed:\n got %+v\nwant %+v", got, before)
	}
	if before.BinaryPath != PythonAgentBinary || len(before.BinaryArgs) != 0 || before.Native {
		t.Errorf("the seed no longer launches %s bare: %+v", PythonAgentBinary, before)
	}
}

func TestGaiacppIsPickedWhereverTheCatalogLooks(t *testing.T) {
	places := map[string]func(nativeFixture) string{
		"on PATH":                  func(f nativeFixture) string { return f.path },
		"beside the terminal UI":   func(f nativeFixture) string { return f.beside },
		"in the agent install dir": func(f nativeFixture) string { return filepath.Join(f.home, ".gaia", "agents", FlagshipID) },
		"in the install dir's bin": func(f nativeFixture) string { return filepath.Join(f.home, ".gaia", "agents", FlagshipID, "bin") },
	}
	for name, dir := range places {
		t.Run(name, func(t *testing.T) {
			f := isolateNative(t)
			want := writeProgram(t, dir(f), NativeAgentBinary)

			c := NewCatalog()
			if err := selectAgent(c, ""); err != nil {
				t.Fatal(err)
			}
			got := c.Get(FlagshipID)
			if !got.Native || !samePath(t, got.BinaryPath, want) {
				t.Fatalf("gaiacpp %s was not picked: native=%v path=%q", name, got.Native, got.BinaryPath)
			}
			if !reflect.DeepEqual(got.BinaryArgs, []string{NativeStdioFlag}) {
				t.Errorf("args = %v; without %s gaiacpp opens its own session", got.BinaryArgs, NativeStdioFlag)
			}
			if !got.CanonicalEvents {
				t.Error("gaiacpp is parsed as legacy events, which rejects every line it writes")
			}
			if !reflect.DeepEqual(got.DevArgs, []string{"--dev"}) {
				t.Errorf("dev args = %v, want the --dev gaiacpp accepts", got.DevArgs)
			}
			if got.Transport != TransportSubprocess {
				t.Errorf("transport = %v, want subprocess", got.Transport)
			}
		})
	}
}

// A gaiacpp build sitting in a checkout is not an installed agent. Started from
// inside that checkout with nothing asked for, the terminal UI must keep running
// what is installed: the build may be stale, for another OS, or not the user's.
// Asking for gaiacpp by name is what makes the checkout worth searching.
func TestABuildInTheWorkingTreeIsOnlyRunWhenAskedFor(t *testing.T) {
	for _, sub := range []string{"", "Release", "Debug"} {
		t.Run("cpp/build/"+sub, func(t *testing.T) {
			f := isolateNative(t)
			built := writeProgram(t, filepath.Join(f.cwd, "cpp", "build", sub), NativeAgentBinary)
			deeper := filepath.Join(f.cwd, "tui", "internal")
			if err := os.MkdirAll(deeper, 0o755); err != nil {
				t.Fatal(err)
			}
			t.Chdir(deeper)
			before := *NewCatalog().Get(FlagshipID)

			auto := NewCatalog()
			if err := selectAgent(auto, ""); err != nil {
				t.Fatal(err)
			}
			if got := *auto.Get(FlagshipID); !reflect.DeepEqual(got, before) {
				t.Fatalf("a build in a parent folder replaced the installed agent unasked:\n got %+v\nwant %+v", got, before)
			}
			if l := FindNative(false); l.Found() || strings.Contains(strings.Join(l.Looked, " "), "cpp/build") {
				t.Errorf("the unasked search still reaches the working tree: %+v", l)
			}

			asked := NewCatalog()
			if err := selectAgent(asked, NativeAgentBinary); err != nil {
				t.Fatalf("gaiacpp asked for by name was not found in the checkout: %v", err)
			}
			if got := asked.Get(FlagshipID); !got.Native || !samePath(t, got.BinaryPath, built) {
				t.Errorf("asked for by name, the checkout's build was not run: %+v", got)
			}
		})
	}
}

// Both installed: the native agent is the terminal UI's, unless asked otherwise.
func TestTheOverrideRunsThePythonAgentBesideAnInstalledGaiacpp(t *testing.T) {
	f := isolateNative(t)
	writeProgram(t, f.path, NativeAgentBinary)
	python := writeProgram(t, f.path, PythonAgentBinary)

	auto := NewCatalog()
	if err := selectAgent(auto, ""); err != nil || !auto.Get(FlagshipID).Native {
		t.Fatalf("with both installed the native agent was not picked: %v", err)
	}

	c := NewCatalog()
	if err := selectAgent(c, PythonAgentBinary); err != nil {
		t.Fatal(err)
	}
	got := c.Get(FlagshipID)
	if got.Native || len(got.BinaryArgs) != 0 || !samePath(t, got.BinaryPath, python) {
		t.Errorf("asking for %s did not run it bare: %+v", PythonAgentBinary, got)
	}
}

// An install record can put the flagship behind the daemon, which runs the
// Python agent. gaiacpp is spawned by the terminal UI itself, so picking it has
// to bring the entry back to the subprocess transport, or it would be marked
// native and still be answered by Python.
func TestGaiacppIsSpawnedDirectlyEvenWhenTheInstallRecordSaysDaemon(t *testing.T) {
	f := isolateNative(t)
	dir := filepath.Join(f.home, ".gaia", "agents", FlagshipID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	record := `{"id":"gaia","version":"0.1.1","artifact_kind":"binary"}`
	if err := os.WriteFile(filepath.Join(dir, SentinelName), []byte(record), 0o600); err != nil {
		t.Fatal(err)
	}

	without := NewCatalog()
	without.DiscoverBinaries()
	if err := selectAgent(without, ""); err != nil {
		t.Fatal(err)
	}
	if got := without.Get(FlagshipID); got.Transport != TransportDaemon || got.Native {
		t.Fatalf("without gaiacpp the install record no longer decides: %+v", got)
	}

	writeProgram(t, f.beside, NativeAgentBinary)
	c := NewCatalog()
	c.DiscoverBinaries()
	if err := selectAgent(c, ""); err != nil {
		t.Fatal(err)
	}
	if got := c.Get(FlagshipID); got.Transport != TransportSubprocess || !got.Native {
		t.Errorf("gaiacpp was picked but would be reached through the daemon: %+v", got)
	}

}

// On such an install the file named gaia-agent in the install directory is the
// daemon's HTTP sidecar, so the bare name cannot mean "spawn that". It means the
// Python agent as installed, and the user is told it answers through the
// background service. A path is one particular program, and is spawned.
func TestAskingForThePythonAgentOnADaemonInstallIsNeverSilent(t *testing.T) {
	f := isolateNative(t)
	dir := filepath.Join(f.home, ".gaia", "agents", FlagshipID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	record := `{"id":"gaia","version":"0.1.1","artifact_kind":"binary"}`
	if err := os.WriteFile(filepath.Join(dir, SentinelName), []byte(record), 0o600); err != nil {
		t.Fatal(err)
	}
	sidecar := writeProgram(t, dir, PythonAgentBinary)

	named := NewCatalog()
	named.DiscoverBinaries()
	notice, err := named.SelectFlagshipAgent(PythonAgentBinary)
	if err != nil {
		t.Fatalf("the installed Python agent was refused: %v", err)
	}
	if notice != DaemonPythonNotice || !strings.Contains(notice, "background service") ||
		!strings.Contains(notice, "full path") {
		t.Errorf("the user was not told how their request was honoured: %q", notice)
	}
	got := named.Get(FlagshipID)
	if got.Transport != TransportDaemon || got.Native {
		t.Errorf("the bare name moved the flagship off the daemon, where %s would be spawned: %+v", sidecar, got)
	}

	stdio := writeProgram(t, t.TempDir(), PythonAgentBinary)
	pathed := NewCatalog()
	pathed.DiscoverBinaries()
	if err := selectAgent(pathed, stdio); err != nil {
		t.Fatal(err)
	}
	got = pathed.Get(FlagshipID)
	if got.Transport != TransportSubprocess || got.Native || !samePath(t, got.BinaryPath, stdio) ||
		len(got.BinaryArgs) != 0 || !got.CanonicalEvents {
		t.Errorf("a path to a Python agent was not spawned as given: %+v", got)
	}
}

// Windows users type the file name they see.
func TestTheExeSpellingIsAcceptedOnWindows(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("there is no .exe spelling to accept off Windows")
	}
	f := isolateNative(t)
	writeProgram(t, f.path, NativeAgentBinary)
	python := writeProgram(t, f.path, PythonAgentBinary)

	for _, spelling := range []string{"gaiacpp.exe", "GaiaCpp.EXE"} {
		c := NewCatalog()
		if err := selectAgent(c, spelling); err != nil || !c.Get(FlagshipID).Native {
			t.Errorf("%q was not taken as gaiacpp: %v", spelling, err)
		}
	}
	for _, spelling := range []string{"gaia-agent.exe", "GAIA-AGENT.Exe"} {
		c := NewCatalog()
		if err := selectAgent(c, spelling); err != nil || c.Get(FlagshipID).Native ||
			!samePath(t, c.Get(FlagshipID).BinaryPath, python) {
			t.Errorf("%q was not taken as gaia-agent: %v", spelling, err)
		}
	}
	if err := selectAgent(NewCatalog(), "python.exe"); err == nil {
		t.Error("an unknown program was accepted because it ends in .exe")
	}
}

// A named agent is a demand. Missing, it fails saying where it looked, and the
// other agent is never run in its place.
func TestANamedAgentThatIsMissingFailsNamingWhereItLooked(t *testing.T) {
	t.Run(NativeAgentBinary, func(t *testing.T) {
		f := isolateNative(t)
		writeProgram(t, f.path, PythonAgentBinary)
		c := NewCatalog()
		before := *c.Get(FlagshipID)

		err := selectAgent(c, NativeAgentBinary)
		if !errors.Is(err, ErrNoExecutable) {
			t.Fatalf("a missing gaiacpp did not fail: %v", err)
		}
		for _, want := range []string{NativeAgentBinary, "PATH", f.beside,
			filepath.Join(f.home, ".gaia", "agents", FlagshipID), "cpp/build", AgentChoiceDocsURL} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("the error does not name %q:\n%s", want, err)
			}
		}
		if got := *c.Get(FlagshipID); !reflect.DeepEqual(got, before) {
			t.Errorf("a failed request still changed the entry: %+v", got)
		}
	})

	t.Run(PythonAgentBinary, func(t *testing.T) {
		f := isolateNative(t)
		writeProgram(t, f.path, NativeAgentBinary)
		c := NewCatalog()

		err := selectAgent(c, PythonAgentBinary)
		if !errors.Is(err, ErrNoExecutable) {
			t.Fatalf("a missing %s did not fail: %v", PythonAgentBinary, err)
		}
		for _, want := range []string{PythonAgentBinary, "PATH", filepath.Join(f.home, ".gaia", "agents", FlagshipID)} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("the error does not name %q:\n%s", want, err)
			}
		}
		if c.Get(FlagshipID).Native {
			t.Error("gaiacpp was run in place of the Python agent that was asked for")
		}
	})
}

func TestAPathNamesTheAgentProgramDirectly(t *testing.T) {
	f := isolateNative(t)
	elsewhere := t.TempDir()

	native := writeProgram(t, elsewhere, "gaiacpp-windows-x64")
	c := NewCatalog()
	if err := selectAgent(c, native); err != nil {
		t.Fatal(err)
	}
	if got := c.Get(FlagshipID); !got.Native || !samePath(t, got.BinaryPath, native) ||
		!reflect.DeepEqual(got.BinaryArgs, []string{NativeStdioFlag}) {
		t.Errorf("a path to a release build of gaiacpp was not run as the native agent: %+v", got)
	}

	python := writeProgram(t, elsewhere, PythonAgentBinary)
	c = NewCatalog()
	if err := selectAgent(c, python); err != nil {
		t.Fatal(err)
	}
	if got := c.Get(FlagshipID); got.Native || len(got.BinaryArgs) != 0 || !samePath(t, got.BinaryPath, python) {
		t.Errorf("a path to %s was not run as the Python agent: %+v", PythonAgentBinary, got)
	}

	missing := filepath.Join(f.cwd, "nowhere", NativeAgentBinary)
	if err := selectAgent(NewCatalog(), missing); err == nil || !strings.Contains(err.Error(), missing) {
		t.Errorf("a path with nothing at it was accepted, or not named: %v", err)
	}

	other := writeProgram(t, elsewhere, "some-other-agent")
	if err := selectAgent(NewCatalog(), other); err == nil ||
		!strings.Contains(err.Error(), NativeAgentBinary) || !strings.Contains(err.Error(), PythonAgentBinary) {
		t.Errorf("a program of unknown protocol was accepted, or the error names no valid choice: %v", err)
	}
}

func TestAnUnknownAgentNameIsRefusedWithTheValidOnes(t *testing.T) {
	isolateNative(t)
	err := selectAgent(NewCatalog(), "python")
	if err == nil {
		t.Fatal("an unknown agent name was accepted")
	}
	for _, want := range []string{`"python"`, NativeAgentBinary, PythonAgentBinary} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not mention %s:\n%s", want, err)
		}
	}
}

func TestIsNativeAgentBinary(t *testing.T) {
	cases := map[string]bool{
		"gaiacpp":                            true,
		"gaiacpp.exe":                        true,
		"GAIACPP.EXE":                        true,
		"/opt/gaia/gaiacpp-linux-x64":        true,
		`C:\tools\gaiacpp-windows-x64.exe`:   true,
		"gaia-agent":                         false,
		"gaia-agent.exe":                     false,
		"gaiacppish":                         false,
		filepath.Join("gaiacpp", "gaia-tui"): false,
	}
	for path, want := range cases {
		if got := IsNativeAgentBinary(path); got != want {
			t.Errorf("IsNativeAgentBinary(%q) = %v, want %v", path, got, want)
		}
	}
}

// A mock speaks the legacy dialect and declares no flags, whichever agent the
// entry it replaces was on.
func TestSetMockBinaryReplacesTheNativeAgentToo(t *testing.T) {
	f := isolateNative(t)
	writeProgram(t, f.path, NativeAgentBinary)
	c := NewCatalog()
	if err := selectAgent(c, ""); err != nil {
		t.Fatal(err)
	}
	c.SetMockBinary("/tmp/mock-agent")
	got := c.Get(FlagshipID)
	if got.Native || got.CanonicalEvents || len(got.BinaryArgs) != 0 || got.BinaryPath != "/tmp/mock-agent" {
		t.Errorf("--mock left the native agent's settings behind: %+v", got)
	}
}
