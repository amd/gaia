package catalog

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

// Catalog manages the agent registry.
type Catalog struct {
	agents   []Agent
	warnings []string
}

// NewCatalog creates a catalog with hardcoded seed agents.
func NewCatalog() *Catalog {
	return &Catalog{agents: seedAgents()}
}

// All returns all agents.
func (c *Catalog) All() []Agent {
	result := make([]Agent, len(c.agents))
	copy(result, c.agents)
	return result
}

// Get returns an agent by ID, or nil if not found.
func (c *Catalog) Get(id string) *Agent {
	for i := range c.agents {
		if c.agents[i].ID == id {
			return &c.agents[i]
		}
	}
	return nil
}

// DiscoverBinaries searches for agent executables on PATH, in the hub install
// root, and in common build locations.
// Daemon-transport agents are skipped — the daemon owns their lifecycle.
func (c *Catalog) DiscoverBinaries() {
	for i := range c.agents {
		if c.agents[i].Transport == TransportDaemon || c.agents[i].BinaryPath == "" {
			continue
		}
		name := c.agents[i].BinaryPath
		// Check if already on PATH
		if p, err := exec.LookPath(name); err == nil {
			c.agents[i].BinaryPath = p
			continue
		}
		if p, err := exec.LookPath(name + ".exe"); err == nil {
			c.agents[i].BinaryPath = p
			continue
		}
		// An agent installed from the Agent Hub lives under ~/.gaia/agents/<id>/.
		if found := findInstalledBinary(c.agents[i].ID, name); found != "" {
			c.agents[i].BinaryPath = found
			continue
		}
		// Finally the in-repo build output, for a developer running from source.
		if found := findBinaryInRepo(name); found != "" {
			c.agents[i].BinaryPath = found
		}
	}

	// Sentinels are read AFTER the binary lookup: applying them first flips
	// every installed id to daemon transport, and the loop above skips daemon
	// agents — which made the install-root lookup unreachable.
	c.LoadInstalledAgents()
}

// SentinelName is the file gaia.hub.installer writes into an agent's install
// directory when the install completes. Its presence IS the installed state.
const SentinelName = ".installed"

// InstallRoot is the directory the daemon installs hub agents into. It mirrors
// gaia.hub.installer.default_install_root() exactly — a client that looked
// somewhere else would report an agent as missing that is sitting on disk.
func InstallRoot() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".gaia", "agents")
}

// executableNames returns the candidate file names for a binary on this OS.
// The old lookup hardcoded ".exe", which can never match on macOS or Linux.
func executableNames(name string) []string {
	if runtime.GOOS == "windows" {
		return []string{name + ".exe", name}
	}
	return []string{name, name + ".exe"}
}

// findInstalledBinary looks for an agent binary inside its hub install
// directory (~/.gaia/agents/<id>/, optionally under bin/).
func findInstalledBinary(agentID, name string) string {
	return findInstalledBinaryIn(InstallRoot(), agentID, name)
}

// findInstalledBinaryIn returns the agent's binary under the install root, but
// only when an .installed sentinel proves the directory is a completed install.
//
// Without that gate the file's NAME is the only evidence of what it is, and the
// name is not unique: `gaia-agent` is both the stdio child this looks for and
// the frozen REST sidecar other installers stage into this same directory.
// Spawning the wrong one feeds uvicorn's startup log to a JSON line scanner
// (#3062). A directory with no sentinel is a leftover or an in-progress
// install, which is how LocalInstalls already treats it.
func findInstalledBinaryIn(root, agentID, name string) string {
	if root == "" || agentID == "" {
		return ""
	}
	if record, err := readSentinel(filepath.Join(root, agentID, SentinelName)); err != nil || record == nil {
		return ""
	}
	return installDirBinaryIn(root, agentID, name)
}

// installDirBinaryIn finds the binary by name alone, with no sentinel check.
// Only two callers may use it: findInstalledBinaryIn once the sentinel has
// verified the install, and ResolveExecutable's diagnostic, which needs to tell
// "nothing is there" apart from "something is there but unverified".
func installDirBinaryIn(root, agentID, name string) string {
	if root == "" || agentID == "" {
		return ""
	}
	dirs := []string{
		filepath.Join(root, agentID),
		filepath.Join(root, agentID, "bin"),
	}
	for _, dir := range dirs {
		for _, candidate := range executableNames(name) {
			p := filepath.Join(dir, candidate)
			if isExecutableFile(p) {
				abs, err := filepath.Abs(p)
				if err != nil {
					return p
				}
				return abs
			}
		}
	}
	return ""
}

// findBinaryInRepo walks up the directory tree from cwd looking for the agent binary
// in common build output locations (cpp/build/Debug/, cpp/build/Release/).
func findBinaryInRepo(name string) string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for i := 0; i < 8; i++ {
		for _, buildDir := range []string{"Debug", "Release", ""} {
			for _, candidate := range executableNames(name) {
				var p string
				if buildDir != "" {
					p = filepath.Join(dir, "cpp", "build", buildDir, candidate)
				} else {
					p = filepath.Join(dir, "cpp", "build", candidate)
				}
				if isExecutableFile(p) {
					abs, aerr := filepath.Abs(p)
					if aerr != nil {
						return p
					}
					return abs
				}
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return ""
}

// ErrNoExecutable is returned by ResolveExecutable when nothing runnable is
// found. Callers match on it to tell "cannot start" from "started and failed".
var ErrNoExecutable = errors.New("no runnable binary")

// InstallerURL is where a user gets the GAIA installer, which ships the agent
// binaries alongside the TUI.
const InstallerURL = "https://amd-gaia.ai/#install"

// AgentDocsURL is the flagship agent's guide.
const AgentDocsURL = "https://amd-gaia.ai/docs/guides/gaia"

// Lookup is where an agent binary was found, or everywhere it was looked for.
//
// It exists because the three outcomes need three different things said about
// them, and a caller that only has an error string has to parse prose to tell
// them apart: nothing anywhere (re-run the installer), a file under the install
// root with no sentinel (finish the install), and a resolved path (run it).
type Lookup struct {
	// Path is the executable to run. Empty when there is nothing to run.
	Path string
	// Unverified is a file under the install root with no .installed sentinel:
	// it is there, but nothing proves WHAT it is — `gaia-agent` is both the
	// stdio child this looks for and the frozen REST sidecar other installers
	// stage into the same directory (#3062). Never offered as Path.
	Unverified string
	// Looked is every place searched, in order, so a failure can say where.
	Looked []string
	// PresenceOnly is true when the only thing established about Path is that a
	// file is sitting there — see presenceOnly. A caller must report "found",
	// never "ready".
	PresenceOnly bool
}

// Found reports whether there is something to exec.
func (l Lookup) Found() bool { return l.Path != "" }

// Find locates an agent binary: PATH, then the hub install root, then the
// in-repo build output.
func Find(nameOrPath, agentID string) Lookup {
	if nameOrPath == "" {
		return Lookup{}
	}
	// An explicit path is taken at its word — it is not searched for.
	if isPath(nameOrPath) {
		l := Lookup{Looked: []string{nameOrPath}}
		if isExecutableFile(nameOrPath) {
			l.Path = nameOrPath
			if abs, err := filepath.Abs(nameOrPath); err == nil {
				l.Path = abs
			}
			l.PresenceOnly = presenceOnly(l.Path)
		}
		return l
	}

	installDir := InstallRoot()
	if installDir == "" {
		installDir = filepath.Join("~", ".gaia", "agents", agentID)
	} else {
		installDir = filepath.Join(installDir, agentID)
	}
	l := Lookup{Looked: []string{"your PATH", installDir, repoBuildDir}}

	for _, candidate := range executableNames(nameOrPath) {
		if p, err := exec.LookPath(candidate); err == nil {
			// LookPath honours PATHEXT, so anything it returns is runnable.
			l.Path = p
			return l
		}
	}
	for _, find := range []func() string{
		func() string { return findInstalledBinary(agentID, nameOrPath) },
		func() string { return findBinaryInRepo(nameOrPath) },
	} {
		if p := find(); p != "" {
			l.Path = p
			l.PresenceOnly = presenceOnly(p)
			return l
		}
	}
	// Nothing runnable — but a file may still be sitting in the install
	// directory with no sentinel to say what it is. "Not found" would send the
	// user hunting for a missing download when the answer is "finish the
	// install"; naming the file is the difference.
	l.Unverified = installDirBinaryIn(InstallRoot(), agentID, nameOrPath)
	return l
}

// repoBuildDir is the only in-repo location findBinaryInRepo searches. Named
// here so a failure message and the search itself cannot disagree.
const repoBuildDir = "./cpp/build/"

// isPath reports whether s names a location rather than a program to look up.
//
// BOTH separators, on every OS: Windows accepts forward slashes everywhere, and
// testing only os.PathSeparator there sent "C:/tools/gaia-agent" down the
// search-by-name branch — which then reported it as missing from PATH and named
// three places it had never looked for it.
func isPath(s string) bool {
	return strings.ContainsAny(s, `/\`) || strings.HasPrefix(s, ".")
}

// ResolveExecutable turns an agent's BinaryPath into a path this process can
// actually exec, or fails saying where it looked. Discovery leaves an
// unresolved NAME in place, so checking BinaryPath != "" let a launch report
// "connected" for a binary that does not exist.
func ResolveExecutable(nameOrPath, agentID string) (string, error) {
	if nameOrPath == "" {
		return "", fmt.Errorf("%w: the catalog entry names no binary", ErrNoExecutable)
	}
	l := Find(nameOrPath, agentID)
	if l.Found() {
		return l.Path, nil
	}
	if isPath(nameOrPath) {
		return "", fmt.Errorf("%w: %s is not an executable file", ErrNoExecutable, nameOrPath)
	}
	if l.Unverified != "" {
		return "", fmt.Errorf(
			"%w: reinstall %s with `gaia hub install %s` — %s exists but the install "+
				"is unverified (no %s), so it is not safe to run",
			ErrNoExecutable, agentID, agentID, l.Unverified, SentinelName)
	}
	return "", errors.New(MissingBinaryMessage(nameOrPath, l))
}

// MissingBinaryMessage is what a user is told when an agent's program is not on
// the machine. It is the same text on the readiness screen and on stderr from a
// one-shot, so the two can never say different things about the same state.
//
// It names the installer, not a build-from-source step: GAIA ships one agent
// and the installer is what puts it there. A TUI that offered to download it
// instead would be fetching ~90 MB over an unverified path.
func MissingBinaryMessage(binary string, l Lookup) string {
	return fmt.Sprintf(
		"%s is the program that does the thinking, and it is not on this machine. "+
			"Nothing runs without it.\nLooked in:  %s\n"+
			"Fix:        re-run the GAIA installer — it ships %s alongside gaia-tui\n"+
			"            %s\nlook:       %s",
		binary, strings.Join(l.Looked, ", "), binary, InstallerURL, AgentDocsURL)
}

// isExecutableFile reports whether path is a regular file this process could
// exec. On Windows the mode bits carry no exec information, so existence is the
// only usable test there — see Lookup.PresenceOnly for what that costs.
func isExecutableFile(path string) bool {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return false
	}
	if runtime.GOOS == "windows" {
		return true
	}
	return info.Mode().Perm()&0o111 != 0
}

// presenceOnly reports whether all that was established about path is that a
// file is sitting there.
//
// On Windows the mode bits carry no exec information, so isExecutableFile
// accepts any regular file. A PATHEXT extension is at least the same evidence a
// Unix exec bit gives — the OS will try to run it — so only an extensionless
// match is a bare existence check. Neither platform proves the binary was built
// for this architecture; only starting it does.
func presenceOnly(path string) bool {
	if runtime.GOOS != "windows" || path == "" {
		return false
	}
	switch strings.ToLower(filepath.Ext(path)) {
	case ".exe", ".com", ".bat", ".cmd":
		return false
	}
	return true
}

// SetMockBinary points every subprocess agent at a mock binary and marks those
// agents installed: --mock IS the claim that a runnable binary exists.
// Daemon agents are skipped.
func (c *Catalog) SetMockBinary(binaryPath string) {
	for i := range c.agents {
		// A binary path in the entry is what makes it a subprocess agent at all;
		// an entry that names none has nothing for a mock to stand in for.
		if c.agents[i].Transport == TransportDaemon || c.agents[i].BinaryPath == "" {
			continue
		}
		c.agents[i].BinaryPath = binaryPath
		// All four describe how to talk to ONE binary, so they are replaced as
		// a unit — a mock inheriting the real agent's --dev would be handed an
		// argument it never declared, and one inheriting CanonicalEvents has
		// every line it writes rejected as "unsupported event".
		c.agents[i].BinaryArgs = nil
		c.agents[i].DevArgs = nil
		c.agents[i].CanonicalEvents = false
		c.agents[i].Status = StatusInstalled
	}
}

// SetStatus updates an agent's status.
func (c *Catalog) SetStatus(id string, status AgentStatus) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Status = status
			return
		}
	}
}

// InstalledRecord is one ~/.gaia/agents/<id>/.installed sentinel, the local
// source of truth for "this agent is installed" (gaia.hub.installer).
type InstalledRecord struct {
	ID         string `json:"id"`
	Version    string `json:"version"`
	Language   string `json:"language"`
	Executable string `json:"executable"`
}

// Warnings returns problems found while reading local state — an unreadable
// install root, a corrupt sentinel. They are surfaced in the UI rather than
// logged and forgotten: every one of them makes an installed agent silently
// disappear from the hub, which looks identical to "never installed".
func (c *Catalog) Warnings() []string {
	return append([]string(nil), c.warnings...)
}

func (c *Catalog) warn(format string, args ...any) {
	c.warnings = append(c.warnings, fmt.Sprintf(format, args...))
}

// LocalInstalls reads the ~/.gaia/agents/*/.installed sentinels — the local
// record of what is installed. It needs no daemon and no network, which is what
// makes `gaia tui list --installed` answerable offline.
//
// The second return is warnings: every one of them makes an installed agent
// silently disappear, which looks identical to "never installed", so callers
// must show them rather than drop them.
func LocalInstalls() ([]InstalledRecord, []string) {
	var (
		records  []InstalledRecord
		warnings []string
	)
	warn := func(format string, args ...any) {
		warnings = append(warnings, fmt.Sprintf(format, args...))
	}

	root := InstallRoot()
	if root == "" {
		warn("cannot resolve the home directory, so installed agents under " +
			"~/.gaia/agents could not be found")
		return nil, warnings
	}
	entries, err := os.ReadDir(root)
	if errors.Is(err, fs.ErrNotExist) {
		// No install root yet is the normal fresh-machine state.
		return nil, nil
	}
	if err != nil {
		warn("cannot read %s (%v), so installed agents are not listed", root, err)
		return nil, warnings
	}
	for _, entry := range entries {
		if !entry.IsDir() || strings.HasPrefix(entry.Name(), ".") {
			continue
		}
		path := filepath.Join(root, entry.Name(), SentinelName)
		record, serr := readSentinel(path)
		if serr != nil {
			warn("%s is unreadable (%v), so '%s' is not listed as installed — reinstall it",
				path, serr, entry.Name())
			continue
		}
		if record == nil {
			continue // no sentinel: a leftover or in-progress directory
		}
		if record.ID == "" {
			record.ID = entry.Name()
		}
		records = append(records, *record)
	}
	sort.SliceStable(records, func(i, j int) bool { return records[i].ID < records[j].ID })
	return records, warnings
}

// LoadInstalledAgents merges the local install sentinels into the catalog.
//
// It needs no daemon and no network, so an agent installed from the hub is
// runnable (`gaia tui run <id>`) even when the catalog fetch fails — and an
// agent that is on disk is never shown as "Available".
func (c *Catalog) LoadInstalledAgents() {
	records, warnings := LocalInstalls()
	c.warnings = append(c.warnings, warnings...)
	for _, record := range records {
		c.applyInstalledRecord(record.ID, record.Version)
	}
}

// applyInstalledRecord records what a sentinel actually proves — that this id
// is installed at this version — without inventing the metadata only the hub
// index carries. upsertHubEntry would overwrite a cached name, publisher, tier,
// and size with blanks, degrading "Email · AMD · 31.1 MB" to a bare id in
// exactly the offline case this function exists to serve.
func (c *Catalog) applyInstalledRecord(id, version string) {
	idx := -1
	for i := range c.agents {
		if c.agents[i].ID == id {
			idx = i
			break
		}
	}
	if idx < 0 {
		c.agents = append(c.agents, Agent{
			ID:        id,
			Name:      id,
			Icon:      "📦",
			Category:  "general",
			Transport: TransportDaemon,
		})
		idx = len(c.agents) - 1
	}
	a := &c.agents[idx]
	a.FromHub = true
	// A sentinel under the install root means the daemon installed it as an
	// HTTP sidecar it supervises, so there is no binary for the TUI to spawn --
	// same invariant upsertHubEntry applies. Seeded entries reach here with
	// whatever transport the seed guessed, and a seeded subprocess agent that
	// kept it would be spawned over stdio and fed a frozen REST binary.
	a.Transport = TransportDaemon
	a.InstalledVersion = version
	if version != "" {
		a.Version = version
	}
	a.Status = StatusInstalled
}

// readSentinel returns (nil, nil) when there is simply no sentinel, and an
// error when one exists but cannot be used.
func readSentinel(path string) (*InstalledRecord, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var record InstalledRecord
	if err := json.Unmarshal(raw, &record); err != nil {
		return nil, err
	}
	return &record, nil
}

// FlagshipID is the agent the TUI boots into. GAIA ships one.
const FlagshipID = "gaia"

func seedAgents() []Agent {
	return []Agent{
		{
			// The email agent is an HTTP sidecar the daemon supervises, not a
			// binary the TUI can spawn — it is reached through the daemon relay.
			// Not on the launch path: `gaia-tui chat --agent email` is how it is
			// reached, and it keeps the daemon readiness gate.
			ID: "email", Name: "Email", Description: "Email triage, drafting, and calendar",
			Category: "Productivity", Tags: []string{"email", "gmail", "calendar", "communication"},
			Icon: "📧", Version: "0.1.0", Status: StatusAvailable,
			Transport: TransportDaemon,
		},
		// The flagship, spawned directly as a child process: TUI -> agent ->
		// Lemonade, with no daemon, HTTP port, bearer token or model-slot lease
		// in the path. The child is started once and kept, which is what makes a
		// turn cost ~2.5s instead of ~44.6s (the agent is built once, not per
		// request) and what makes a skill loaded in one turn still loaded in the
		// next — no session id, no session registry, no contract version.
		//
		// CanonicalEvents because it writes the canonical vocabulary, the only
		// one with somewhere to put tool narration and result previews.
		{
			ID: FlagshipID, Name: "GAIA", Description: "Chat, documents, data, and web research — with memory and skills",
			Category: "General", Tags: []string{"general", "chat", "rag", "memory", "skills"},
			Icon: "✨", Version: "0.1.0", Status: StatusInstalled,
			Transport:       TransportSubprocess,
			BinaryPath:      "gaia-agent",
			CanonicalEvents: true,
			// `gaia-tui --dev` also puts the child at DEBUG in
			// ~/.gaia/logs/gaia-agent.log. Without this the TUI would go verbose
			// while the agent kept logging errors only — and the log is where the
			// answer usually is.
			DevArgs: []string{"--dev"},
		},
	}
}
