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

// Section represents a tab/section in the hub UI.
type Section string

const (
	SectionDashboard  Section = "Dashboard"
	SectionInstalled  Section = "Installed"
	SectionAvailable  Section = "Available"
	SectionComingSoon Section = "Coming Soon"
)

// AllSections returns the tab order for the hub.
func AllSections() []Section {
	return []Section{SectionInstalled, SectionAvailable, SectionComingSoon}
}

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

func findInstalledBinaryIn(root, agentID, name string) string {
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

// ResolveExecutable turns an agent's BinaryPath into a path this process can
// actually exec, or fails saying where it looked. Discovery leaves an
// unresolved NAME in place, so checking BinaryPath != "" let a launch report
// "connected" for a binary that does not exist.
func ResolveExecutable(nameOrPath, agentID string) (string, error) {
	if nameOrPath == "" {
		return "", fmt.Errorf("%w: the catalog entry names no binary", ErrNoExecutable)
	}
	if strings.ContainsRune(nameOrPath, os.PathSeparator) || strings.HasPrefix(nameOrPath, ".") {
		if isExecutableFile(nameOrPath) {
			if abs, err := filepath.Abs(nameOrPath); err == nil {
				return abs, nil
			}
			return nameOrPath, nil
		}
		return "", fmt.Errorf("%w: %s is not an executable file", ErrNoExecutable, nameOrPath)
	}
	for _, candidate := range executableNames(nameOrPath) {
		if p, err := exec.LookPath(candidate); err == nil {
			return p, nil
		}
	}
	if p := findInstalledBinary(agentID, nameOrPath); p != "" {
		return p, nil
	}
	if p := findBinaryInRepo(nameOrPath); p != "" {
		return p, nil
	}
	where := InstallRoot()
	if where == "" {
		where = "~/.gaia/agents"
	} else {
		where = filepath.Join(where, agentID)
	}
	// Action first: this text is also shown in the hub's one-row status bar,
	// where an 80-column terminal truncates whatever comes after ~70 characters.
	return "", fmt.Errorf(
		"%w: build %s from source, or run an agent the Agent Hub publishes (`gaia tui list`) "+
			"— it is not on PATH, not in %s, and not in cpp/build",
		ErrNoExecutable, nameOrPath, where)
}

// isExecutableFile reports whether path is a regular file this process could
// exec. On Windows the mode bits carry no exec information, so existence is the
// only usable test there.
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

// SetMockBinary points every subprocess agent at a mock binary and marks those
// agents installed: --mock IS the claim that a runnable binary exists, and no
// seed ships installed for it to attach to otherwise. Daemon agents are skipped.
func (c *Catalog) SetMockBinary(binaryPath string) {
	for i := range c.agents {
		// A binary path in the entry is what makes it a subprocess agent at all;
		// an entry that names none has nothing for a mock to stand in for.
		if c.agents[i].Transport == TransportDaemon || c.agents[i].BinaryPath == "" {
			continue
		}
		c.agents[i].BinaryPath = binaryPath
		c.agents[i].BinaryArgs = nil
		if !c.agents[i].Status.IsLaunchable() {
			c.agents[i].Status = StatusInstalled
			c.agents[i].NotOfferedReason = ""
		}
	}
}

// BySection returns agents filtered by their install status section.
func (c *Catalog) BySection(section Section) []Agent {
	var result []Agent
	for _, a := range c.agents {
		switch section {
		case SectionInstalled:
			if a.Status == StatusInstalled || a.Status == StatusActive || a.Status == StatusIdle {
				result = append(result, a)
			}
		case SectionAvailable:
			if a.Status == StatusAvailable {
				result = append(result, a)
			}
		case SectionComingSoon:
			if a.Status == StatusComingSoon {
				result = append(result, a)
			}
		}
	}
	return result
}

// DashboardStats returns counts for the hub dashboard.
func (c *Catalog) DashboardStats() (installed, active, idle int) {
	for _, a := range c.agents {
		switch a.Status {
		case StatusInstalled:
			installed++
		case StatusActive:
			active++
		case StatusIdle:
			idle++
		}
	}
	return
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
	a.InstalledVersion = version
	if version != "" {
		a.Version = version
	}
	if !a.Status.IsLaunchable() {
		a.Status = StatusInstalled
	}
	a.NotOfferedReason = ""
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

// ApplyHubCatalog merges a GET /daemon/v1/catalog response into the catalog.
//
// Hub rows are authoritative for everything the daemon can manage: version,
// download size, security tier, publisher, and installed state. Seed entries the
// hub does not offer are demoted from "Available" to "Coming Soon" with a
// reason — an agent the daemon cannot fetch or start must never sit under a tab
// that promises it can be installed. That is the dead end the design bar
// forbids, and it is why the daemon reports `unsupervised_filtered` instead of
// silently hiding ids.
func (c *Catalog) ApplyHubCatalog(hub *HubCatalog) {
	if hub == nil {
		return
	}
	unsupervised := make(map[string]bool, len(hub.UnsupervisedFiltered))
	for _, id := range hub.UnsupervisedFiltered {
		unsupervised[id] = true
	}

	seen := make(map[string]bool, len(hub.Agents))
	for _, entry := range hub.Agents {
		if entry.ID == "" {
			continue
		}
		seen[entry.ID] = true
		c.upsertHubEntry(entry)
	}

	for i := range c.agents {
		a := &c.agents[i]
		if seen[a.ID] || a.FromHub {
			continue
		}
		// "Published, but nothing here can run it" is a fact the seed's blanket
		// "not published yet" does not have; everything else keeps its own.
		if a.Status == StatusComingSoon {
			if unsupervised[a.ID] {
				a.NotOfferedReason = "no way to run it yet"
			}
			continue
		}
		if a.Status != StatusAvailable {
			continue
		}
		// Listed as Available by the seed catalog but absent from the hub: it
		// cannot be installed, so say so instead of offering it.
		a.Status = StatusComingSoon
		switch {
		case unsupervised[a.ID]:
			a.NotOfferedReason = "no way to run it yet"
		case hub.Offline:
			// The list is a cache, so "not published" would be a claim this
			// data cannot support.
			a.NotOfferedReason = "not in the cached agent list"
		default:
			a.NotOfferedReason = "not on the Agent Hub yet"
		}
	}

	sort.SliceStable(c.agents, func(i, j int) bool { return c.agents[i].ID < c.agents[j].ID })
}

func (c *Catalog) upsertHubEntry(entry HubEntry) {
	idx := -1
	for i := range c.agents {
		if c.agents[i].ID == entry.ID {
			idx = i
			break
		}
	}
	if idx < 0 {
		c.agents = append(c.agents, Agent{
			ID:        entry.ID,
			Name:      entry.ID,
			Icon:      "📦",
			Category:  "general",
			Transport: TransportDaemon,
		})
		idx = len(c.agents) - 1
	}

	a := &c.agents[idx]
	a.FromHub = true
	// Everything the daemon serves from the hub is an HTTP sidecar it
	// supervises; there is no binary for the TUI to spawn.
	a.Transport = TransportDaemon
	if entry.Name != "" {
		a.Name = entry.Name
	}
	if entry.Description != "" {
		a.Description = entry.Description
	}
	if entry.Category != "" {
		a.Category = entry.Category
	}
	if entry.Icon != "" {
		a.Icon = entry.Icon
	}
	if len(entry.Tags) > 0 {
		a.Tags = entry.Tags
	}
	a.Author = entry.Author
	a.SecurityTier = entry.SecurityTier
	a.Permissions = entry.Permissions
	a.DownloadSizeBytes = entry.DownloadSizeBytes
	a.LatestVersion = entry.LatestVersion
	a.InstalledVersion = entry.InstalledVersion
	a.UpdateAvailable = entry.UpdateAvailable
	a.Supervised = entry.Supervised
	a.NotOfferedReason = ""

	switch {
	case entry.Installed:
		a.Version = entry.InstalledVersion
		// Never clobber a live session: an agent the user is chatting with is
		// Active, and Active is also "installed".
		if !a.Status.IsLaunchable() {
			a.Status = StatusInstalled
		}
	case !entry.Supervised:
		a.Version = entry.LatestVersion
		a.Status = StatusComingSoon
		a.NotOfferedReason = "no way to run it yet"
	default:
		a.Version = entry.LatestVersion
		a.Status = StatusAvailable
	}
}

// MarkInstalled records a completed hub install locally so the row flips
// without waiting for the next catalog fetch.
func (c *Catalog) MarkInstalled(id, version string) {
	for i := range c.agents {
		if c.agents[i].ID != id {
			continue
		}
		c.agents[i].Status = StatusInstalled
		if version != "" {
			c.agents[i].InstalledVersion = version
			c.agents[i].Version = version
		} else if c.agents[i].LatestVersion != "" {
			c.agents[i].InstalledVersion = c.agents[i].LatestVersion
			c.agents[i].Version = c.agents[i].LatestVersion
		}
		c.agents[i].UpdateAvailable = false
		return
	}
}

// Remove puts an agent back to its pre-install state. That is NOT
// unconditionally Available: an entry the hub does not offer, or one the daemon
// cannot start, lands back under Coming Soon rather than advertising an install
// the backend cannot honour.
func (c *Catalog) Remove(id string) {
	for i := range c.agents {
		if c.agents[i].ID != id {
			continue
		}
		a := &c.agents[i]
		a.BinaryPath = ""
		a.BinaryArgs = nil
		a.InstalledVersion = ""
		a.UpdateAvailable = false
		if a.LatestVersion != "" {
			a.Version = a.LatestVersion
		}
		switch {
		case a.FromHub && a.Supervised:
			a.Status = StatusAvailable
			a.NotOfferedReason = ""
		case a.FromHub:
			// Published, but this GAIA build has no way to run it.
			a.Status = StatusComingSoon
			a.NotOfferedReason = "no way to run it yet"
		default:
			a.Status = StatusComingSoon
			if a.NotOfferedReason == "" {
				a.NotOfferedReason = NotPublishedReason
			}
		}
		return
	}
}

// IncrementVotes bumps the vote count for a coming-soon agent.
func (c *Catalog) IncrementVotes(id string) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Votes++
			return
		}
	}
}

// DecrementVotes takes back an optimistic increment whose vote never landed.
// Nothing queues a failed vote, so leaving the count up would show the user a
// vote that was never recorded anywhere.
func (c *Catalog) DecrementVotes(id string) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			if c.agents[i].Votes > 0 {
				c.agents[i].Votes--
			}
			return
		}
	}
}

// SetVotes replaces the local count with the server's authoritative one.
func (c *Catalog) SetVotes(id string, votes int) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Votes = votes
			return
		}
	}
}

// NotPublishedReason is why a seed entry sits under Coming Soon: the Agent Hub
// does not publish it, so the daemon has no spec to fetch or start it with.
//
// The hub must never offer an action the backend cannot honour, so every seed
// starts here — except `email`, the one published sidecar, which ships as
// Available. A hub row the daemon reports as supervised promotes the rest
// (upsertHubEntry).
const NotPublishedReason = "not published on the Agent Hub yet"

func seedAgents() []Agent {
	return []Agent{
		// A local C++ binary, never published to the hub and absent unless
		// built from source. Seeding it "installed" put a row in front of every
		// user that connected and then failed on the first message.
		{
			ID: "bash", Name: "Bash", Description: "Shell command execution and automation",
			Category: "DevOps", Tags: []string{"shell", "bash", "terminal", "cli"},
			Icon: "🖥️", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
			BinaryPath:       "gaia-bash", BinaryArgs: []string{"--json-events", "--model", "Gemma-4-E4B-it-GGUF"},
		},

		// --- Not published yet (Python agents — no sidecar spec) ---
		{
			ID: "chat", Name: "Chat", Description: "General conversation and Q&A",
			Category: "Conversation", Tags: []string{"chat", "general", "qa"},
			Icon: "💬", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "doc", Name: "Doc", Description: "Document analysis with RAG",
			Category: "Documents", Tags: []string{"documents", "rag", "pdf", "search"},
			Icon: "📄", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "file", Name: "File", Description: "File system navigation and operations",
			Category: "Productivity", Tags: []string{"files", "filesystem", "io"},
			Icon: "📁", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "code", Name: "Code", Description: "Code generation and editing",
			Category: "Code", Tags: []string{"code", "programming", "developer"},
			Icon: "🔧", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "blender", Name: "Blender", Description: "3D scene automation and modeling",
			Category: "Creative", Tags: []string{"3d", "blender", "modeling", "animation"},
			Icon: "🎨", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "jira", Name: "Jira", Description: "Issue tracking and project management",
			Category: "Productivity", Tags: []string{"jira", "issues", "project", "agile"},
			Icon: "🎫", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "docker", Name: "Docker", Description: "Container management and orchestration",
			Category: "DevOps", Tags: []string{"docker", "containers", "kubernetes"},
			Icon: "🐳", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "summarize", Name: "Summarize", Description: "Document and text summarization",
			Category: "Documents", Tags: []string{"summarize", "text", "tldr"},
			Icon: "📝", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			// The email agent is an HTTP sidecar the daemon supervises, not a
			// binary the TUI can spawn — it is reached through the daemon relay.
			ID: "email", Name: "Email", Description: "Email triage, drafting, and calendar",
			Category: "Productivity", Tags: []string{"email", "gmail", "calendar", "communication"},
			Icon: "📧", Version: "0.1.0", Status: StatusAvailable,
			Transport: TransportDaemon,
		},
		// The flagship. Like email, an HTTP sidecar the daemon supervises and
		// the TUI reaches through the relay — not a binary the TUI can spawn.
		//
		// Seeded ComingSoon on purpose: it is not on the Agent Hub yet, and the
		// seed list is the pre-hub-load fallback, so shipping it Available would
		// offer an install the daemon cannot fetch — the same row-that-fails
		// bug gaia-bash caused. A hub row promotes it once it publishes. The
		// daemon can still start it from source (`--mode dev`), and `run`
		// resolves on catalog presence rather than status, so local development
		// works without over-claiming to users.
		{
			ID: "gaia", Name: "GAIA", Description: "Chat, documents, data, and web research — with memory and skills",
			Category: "General", Tags: []string{"general", "chat", "rag", "memory", "skills"},
			Icon: "✨", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
			Transport:        TransportDaemon,
		},

		// --- Coming Soon ---
		{
			ID: "routing", Name: "Routing", Description: "Intelligent agent selection and orchestration",
			Category: "Infrastructure", Tags: []string{"routing", "orchestration", "multi-agent"},
			Icon: "🔀", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "browser", Name: "Browser", Description: "Web browsing and automation",
			Category: "Research", Tags: []string{"browser", "web", "scraping", "automation"},
			Icon: "🌐", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
		{
			ID: "data-analyst", Name: "Data Analyst", Description: "Data analysis and visualization",
			Category: "Data", Tags: []string{"data", "analysis", "charts", "csv", "excel"},
			Icon: "📊", Version: "0.1.0", Status: StatusComingSoon,
			NotOfferedReason: NotPublishedReason,
		},
	}
}
