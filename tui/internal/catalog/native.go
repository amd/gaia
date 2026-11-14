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
// one. The terminal UI runs the native agent when it is installed; a machine
// that only has the Python agent keeps running that.
const (
	// NativeAgentBinary is the C++ agent.
	NativeAgentBinary = "gaiacpp"
	// PythonAgentBinary is the Python flagship's stdio child.
	PythonAgentBinary = "gaia-agent"
	// NativeStdioFlag puts gaiacpp on the stdio protocol; without it the
	// program opens its own interactive session.
	NativeStdioFlag = "--stdio"
	// AgentEnv names the agent program to run: NativeAgentBinary,
	// PythonAgentBinary, or a path to either. Unset picks the native agent when
	// it is installed.
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

// FindNative locates gaiacpp: PATH, beside this program, then the flagship's
// install directory. The in-repo build output is searched only when inRepo is
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
	l := Lookup{Looked: []string{"your PATH"}}
	if beside != "" {
		l.Looked = append(l.Looked, beside)
	}
	l.Looked = append(l.Looked, installDir)
	if inRepo {
		l.Looked = append(l.Looked, repoBuildDir)
	}

	for _, candidate := range executableNames(NativeAgentBinary) {
		if p, err := exec.LookPath(candidate); err == nil {
			l.Path = absOr(p)
			return l
		}
	}
	found := ""
	if beside != "" {
		for _, candidate := range executableNames(NativeAgentBinary) {
			if p := filepath.Join(beside, candidate); isExecutableFile(p) {
				found = p
				break
			}
		}
	}
	if found == "" {
		found = installDirBinaryIn(InstallRoot(), FlagshipID, NativeAgentBinary)
	}
	if found == "" && inRepo {
		found = findBinaryInRepo(NativeAgentBinary)
	}
	if found != "" {
		l.Path = absOr(found)
		l.PresenceOnly = presenceOnly(l.Path)
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
// An empty choice runs gaiacpp when FindNative finds it installed and the
// Python agent otherwise. A named choice is a demand: when that program is not
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
		if l := FindNative(false); l.Found() {
			agent.useNative(l.Path)
		}
		return "", nil

	case strings.EqualFold(name, NativeAgentBinary):
		l := FindNative(true)
		if !l.Found() {
			return "", missingChoiceError(NativeAgentBinary, l)
		}
		agent.useNative(l.Path)
		return "", nil

	case strings.EqualFold(name, PythonAgentBinary):
		if agent.Transport == TransportDaemon {
			// The install record put the Python agent behind the daemon; the
			// file of that name in the install directory is its HTTP sidecar,
			// which must not be spawned as a stdio child.
			return DaemonPythonNotice, nil
		}
		l := Find(PythonAgentBinary, FlagshipID)
		if !l.Found() {
			return "", missingChoiceError(PythonAgentBinary, l)
		}
		agent.BinaryPath = l.Path
		return "", nil

	case isPath(choice):
		if !isExecutableFile(choice) {
			return "", fmt.Errorf(
				"the agent program %s was asked for, but it is not an executable file. "+
					"Fix the path, or name %s or %s to have it found for you. "+
					"See %s", choice, NativeAgentBinary, PythonAgentBinary, AgentChoiceDocsURL)
		}
		switch {
		case IsNativeAgentBinary(choice):
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
		"%q is not an agent program the terminal UI knows. Use %s, %s, or a path to one of them. See %s",
		choice, NativeAgentBinary, PythonAgentBinary, AgentChoiceDocsURL)
}

// DaemonPythonNotice tells a user who named gaia-agent that, on their install,
// the Python agent is reached through the daemon rather than spawned.
const DaemonPythonNotice = "[!] " + PythonAgentBinary + " was asked for. On this install the Python agent " +
	"runs through the GAIA background service, so that is what answers. To run one particular " +
	PythonAgentBinary + " program instead, give its full path."

// AgentChoiceDocsURL explains which agent program the terminal UI runs.
const AgentChoiceDocsURL = "https://amd-gaia.ai/docs/guides/tui"

func missingChoiceError(binary string, l Lookup) error {
	if l.Unverified != "" {
		return fmt.Errorf(
			"%w: %s was asked for, and %s is there, but its install is unfinished (no %s), "+
				"so it is not safe to run. Re-run the GAIA installer (%s), or ask for the other agent program. See %s",
			ErrNoExecutable, binary, l.Unverified, SentinelName, InstallerURL, AgentChoiceDocsURL)
	}
	return fmt.Errorf(
		"%w: %s was asked for, but it is not on this machine.\nLooked in:  %s\n"+
			"Fix:        install it in one of those places, or drop the request to run the agent that is installed\n"+
			"look:       %s",
		ErrNoExecutable, binary, strings.Join(l.Looked, ", "), AgentChoiceDocsURL)
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
