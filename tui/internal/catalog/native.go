package catalog

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// The flagship has two programs behind it: the native C++ agent and the Python
// one. The terminal UI runs the Python agent unless the user chose gaiacpp,
// as the Agent UI and the daemon do.
const (
	// NativeAgentBinary is the C++ agent.
	NativeAgentBinary = "gaiacpp"
	// PythonAgentBinary is the Python flagship's stdio child.
	PythonAgentBinary = "gaia-agent"
	// PythonAgentChoice is the Python agent's name as a choice: the value
	// flagship_agent and /agent take. It is accepted wherever
	// PythonAgentBinary is, so one name works for every way of choosing.
	PythonAgentChoice = "gaiapy"
	// NativeStdioFlag puts gaiacpp on the stdio protocol; without it the
	// program opens its own interactive session.
	NativeStdioFlag = "--stdio"
	// AgentEnv names the agent program to run: NativeAgentBinary,
	// PythonAgentChoice or PythonAgentBinary, or a path to either. Unset leaves the saved choice,
	// else the Python agent.
	AgentEnv = "GAIA_TUI_AGENT"
)

// selfExecutable is os.Executable, swapped out by tests.
var selfExecutable = os.Executable

// IsNativeAgentBinary reports whether path names the C++ agent. Release assets
// carry a platform suffix (gaiacpp-windows-x64.exe), so a prefix counts.
func IsNativeAgentBinary(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	name = strings.TrimSuffix(name, ".exe")
	return name == NativeAgentBinary || strings.HasPrefix(name, NativeAgentBinary+"-")
}

func isPythonAgentBinary(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	return strings.TrimSuffix(name, ".exe") == PythonAgentBinary
}

// gaiaHome is GAIA's home folder. It mirrors gaia.home.gaia_home: GAIA_HOME,
// then GAIA_CONFIG_DIR, then ~/.gaia. Empty when no home directory can be found.
func gaiaHome() string {
	for _, name := range []string{"GAIA_HOME", "GAIA_CONFIG_DIR"} {
		if dir := strings.TrimSpace(os.Getenv(name)); dir != "" {
			return expandHome(os.ExpandEnv(dir))
		}
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ""
	}
	return filepath.Join(home, ".gaia")
}

// gaiaHomeBin is the bin folder of GAIA's home, where gaiacpp is downloaded.
func gaiaHomeBin() string {
	home := gaiaHome()
	if home == "" {
		return ""
	}
	return filepath.Join(home, "bin")
}

// expandHome replaces a leading ~ with the user's home directory.
func expandHome(dir string) string {
	if dir != "~" && !strings.HasPrefix(dir, "~/") && !strings.HasPrefix(dir, `~\`) {
		return dir
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return dir
	}
	return filepath.Join(home, dir[1:])
}

// FindNative locates gaiacpp, in the order the Agent UI's backend uses
// (native_flagship_candidates in src/gaia/daemon/sidecars/fetch.py): the file
// NativePathEnv names, the bin folder of GAIA's home (where the Agent UI
// downloads it), beside this program, PATH, then the flagship's install
// directory. It only finds; VerifyNative checks what was found before it is
// run. The in-repo build output is searched only when inRepo is
// set, which is for a user who asked for gaiacpp by name: unasked, a build left
// in whatever folder the terminal UI was started from must never be run.
//
// The install directory is searched by name alone. The sentinel Find demands
// exists because `gaia-agent` names two different programs; `gaiacpp` names one.
func FindNative(inRepo bool) Lookup {
	installDir := InstallRoot()
	if installDir == "" {
		installDir = filepath.Join("~", ".gaia", "agents")
	}
	installDir = filepath.Join(installDir, FlagshipID)

	beside := ""
	if self, err := selfExecutable(); err == nil {
		beside = filepath.Dir(self)
	}
	explicit := strings.TrimSpace(os.Getenv(NativePathEnv))
	homeBin := gaiaHomeBin()

	var l Lookup
	if explicit != "" {
		l.Looked = append(l.Looked, explicit+" ("+NativePathEnv+")")
	}
	if homeBin != "" {
		l.Looked = append(l.Looked, homeBin)
	}
	if beside != "" {
		l.Looked = append(l.Looked, beside)
	}
	l.Looked = append(l.Looked, "your PATH", installDir)
	if inRepo {
		l.Looked = append(l.Looked, repoBuildDir)
	}

	inDir := func(dir string) string {
		if dir == "" {
			return ""
		}
		for _, candidate := range executableNames(NativeAgentBinary) {
			if p := filepath.Join(dir, candidate); isExecutableFile(p) {
				return p
			}
		}
		return ""
	}
	// First match wins. The places GAIA itself fills come before PATH.
	steps := []func() string{
		func() string {
			if explicit != "" && isExecutableFile(explicit) {
				return explicit
			}
			return ""
		},
		func() string { return inDir(homeBin) },
		func() string { return inDir(beside) },
		func() string {
			for _, candidate := range executableNames(NativeAgentBinary) {
				if p, err := exec.LookPath(candidate); err == nil {
					return p
				}
			}
			return ""
		},
		func() string { return installDirBinaryIn(InstallRoot(), FlagshipID, NativeAgentBinary) },
		func() string {
			if inRepo {
				return findBinaryInRepo(NativeAgentBinary)
			}
			return ""
		},
	}
	for _, step := range steps {
		if found := step(); found != "" {
			l.Path = absOr(found)
			l.PresenceOnly = presenceOnly(l.Path)
			return l
		}
	}
	return l
}

func absOr(p string) string {
	if abs, err := filepath.Abs(p); err == nil {
		return abs
	}
	return p
}

// SelectFlagshipAgent decides which program the flagship entry runs.
//
// An empty choice runs the Python agent, even beside an installed gaiacpp: a
// copy the user never picked must not take away flags only the Python agent
// has. A named choice is a demand: when that program is not
// on the machine this fails, saying where it looked, and never runs the other
// one. The returned notice, when not empty, is something the user must be told
// about how their choice was honoured.
func (c *Catalog) SelectFlagshipAgent(choice string) (notice string, err error) {
	agent := c.Get(FlagshipID)
	if agent == nil {
		return "", fmt.Errorf("the catalog has no %q entry, so there is no agent to select", FlagshipID)
	}
	choice = strings.TrimSpace(choice)
	name := choice
	if runtime.GOOS == "windows" && !isPath(choice) && strings.EqualFold(filepath.Ext(choice), ".exe") {
		name = choice[:len(choice)-len(".exe")]
	}
	switch {
	case choice == "":
		return "", nil

	case strings.EqualFold(name, NativeAgentBinary):
		l := FindNative(true)
		if !l.Found() {
			return "", missingChoiceError(NativeAgentBinary, l)
		}
		if err := VerifyNative(l.Path); err != nil {
			return "", err
		}
		agent.useNative(l.Path)
		return "", nil

	case strings.EqualFold(name, PythonAgentBinary), strings.EqualFold(name, PythonAgentChoice):
		if agent.Transport == TransportDaemon {
			// The install record put the Python agent behind the daemon; the
			// file of that name in the install directory is its HTTP sidecar,
			// which must not be spawned as a stdio child.
			return DaemonPythonNotice, nil
		}
		l := Find(PythonAgentBinary, FlagshipID)
		if !l.Found() {
			asked := PythonAgentBinary
			if strings.EqualFold(name, PythonAgentChoice) {
				asked = PythonAgentChoice + " (the " + PythonAgentBinary + " program)"
			}
			return "", missingChoiceError(asked, l)
		}
		agent.BinaryPath = l.Path
		return "", nil

	case isPath(choice):
		if !isExecutableFile(choice) {
			return "", fmt.Errorf(
				"the agent program %s was asked for, but it is not an executable file. "+
					"Fix the path, or name %s or %s to have it found for you. "+
					"See %s", choice, NativeAgentBinary, PythonAgentChoice, AgentChoiceDocsURL)
		}
		switch {
		case IsNativeAgentBinary(choice):
			if err := VerifyNative(absOr(choice)); err != nil {
				return "", err
			}
			agent.useNative(absOr(choice))
		case isPythonAgentBinary(choice):
			// A path is one particular program: the terminal UI runs it
			// itself, whatever route the install record chose.
			agent.Transport = TransportSubprocess
			agent.BinaryPath = absOr(choice)
		default:
			return "", fmt.Errorf(
				"%s is neither %s nor %s, so the terminal UI cannot tell which protocol it speaks. "+
					"Point at one of those two programs. See %s",
				choice, NativeAgentBinary, PythonAgentBinary, AgentChoiceDocsURL)
		}
		return "", nil
	}
	return "", fmt.Errorf(
		"%q is not an agent program the terminal UI knows. Use %s, %s (or %s), or a path to one of them. See %s",
		choice, NativeAgentBinary, PythonAgentChoice, PythonAgentBinary, AgentChoiceDocsURL)
}

// DaemonPythonNotice tells a user who named the Python agent (gaiapy or
// gaia-agent) that, on their install, it is reached through the daemon rather
// than spawned.
const DaemonPythonNotice = "[!] The Python agent was asked for. On this install it " +
	"runs through the GAIA background service, so that is what answers. To run one particular " +
	PythonAgentBinary + " program instead, give its full path."

// AgentChoiceDocsURL explains which agent program the terminal UI runs.
const AgentChoiceDocsURL = "https://amd-gaia.ai/docs/guides/tui"

// MissingProgramError is a named agent program that is not on this machine.
type MissingProgramError struct {
	Binary string
	Lookup Lookup
}

func (e *MissingProgramError) Unwrap() error { return ErrNoExecutable }

func (e *MissingProgramError) Error() string {
	if e.Lookup.Unverified != "" {
		return fmt.Sprintf(
			"%v: %s was asked for, and %s is there, but its install is unfinished (no %s), "+
				"so it is not safe to run. Re-run the GAIA installer (%s), or ask for the other agent program. See %s",
			ErrNoExecutable, e.Binary, e.Lookup.Unverified, SentinelName, InstallerURL, AgentChoiceDocsURL)
	}
	return fmt.Sprintf(
		"%v: %s was asked for, but it is not on this machine.\nLooked in:  %s\n"+
			"Fix:        %s\n"+
			"look:       %s",
		ErrNoExecutable, e.Binary, strings.Join(e.Lookup.Looked, ", "), e.fix(), AgentChoiceDocsURL)
}

// fix is what to do about it. GAIA downloads gaiacpp itself; nothing else is.
func (e *MissingProgramError) fix() string {
	if e.Binary == NativeAgentBinary {
		return "start GAIA without that request and choose " + NativeAgentBinary +
			" with /agent, which downloads it; or put a build of your own in one of those places"
	}
	return "install it in one of those places, or drop the request to run the agent that is installed"
}

func missingChoiceError(binary string, l Lookup) error {
	return &MissingProgramError{Binary: binary, Lookup: l}
}

// ForFlagship builds the catalog from what is installed, with the flagship on
// the agent program choice names. See SelectFlagshipAgent for choice.
func ForFlagship(choice string) (*Catalog, string, error) {
	cat := NewCatalog()
	cat.DiscoverBinaries()
	notice, err := cat.SelectFlagshipAgent(choice)
	if err != nil {
		return nil, "", err
	}
	return cat, notice, nil
}

// useNative puts the entry on gaiacpp. The transport is set too: an install
// record may have moved the flagship behind the daemon, which runs the Python
// agent, and gaiacpp is only ever a child of this program.
func (a *Agent) useNative(path string) {
	a.Transport = TransportSubprocess
	a.BinaryPath = path
	a.BinaryArgs = []string{NativeStdioFlag}
	a.CanonicalEvents = true
	a.Native = true
}
