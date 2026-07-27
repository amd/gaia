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

// SetMockBinary overrides all installed agent binary paths with a mock binary for testing.
// Daemon-transport agents are skipped — they have no binary to override.
func (c *Catalog) SetMockBinary(binaryPath string) {
	for i := range c.agents {
		if c.agents[i].Transport == TransportDaemon {
			continue
		}
		if c.agents[i].Status == StatusInstalled || c.agents[i].Status == StatusActive || c.agents[i].Status == StatusIdle {
			c.agents[i].BinaryPath = binaryPath
			c.agents[i].BinaryArgs = nil
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

// LoadInstalledAgents merges the local install sentinels into the catalog.
//
// It needs no daemon and no network, so an agent installed from the hub is
// runnable (`gaia tui run <id>`) even when the catalog fetch fails — and an
// agent that is on disk is never shown as "Available".
func (c *Catalog) LoadInstalledAgents() {
	root := InstallRoot()
	if root == "" {
		c.warn("cannot resolve the home directory, so installed agents under " +
			"~/.gaia/agents could not be found")
		return
	}
	entries, err := os.ReadDir(root)
	if errors.Is(err, fs.ErrNotExist) {
		// No install root yet is the normal fresh-machine state.
		return
	}
	if err != nil {
		c.warn("cannot read %s (%v), so installed agents are not listed", root, err)
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() || strings.HasPrefix(entry.Name(), ".") {
			continue
		}
		path := filepath.Join(root, entry.Name(), SentinelName)
		record, serr := readSentinel(path)
		if serr != nil {
			c.warn("%s is unreadable (%v), so '%s' is not listed as installed — reinstall it",
				path, serr, entry.Name())
			continue
		}
		if record == nil {
			continue // no sentinel: a leftover or in-progress directory
		}
		id := record.ID
		if id == "" {
			id = entry.Name()
		}
		c.applyInstalledRecord(id, record.Version)
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
		if seen[a.ID] || a.FromHub || a.Status != StatusAvailable {
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

// Remove removes an agent by setting it back to Available and clearing binary path.
func (c *Catalog) Remove(id string) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Status = StatusAvailable
			c.agents[i].BinaryPath = ""
			c.agents[i].BinaryArgs = nil
			c.agents[i].InstalledVersion = ""
			c.agents[i].UpdateAvailable = false
			if c.agents[i].LatestVersion != "" {
				c.agents[i].Version = c.agents[i].LatestVersion
			}
			// A hub agent the daemon cannot start is still not installable.
			if c.agents[i].FromHub && !c.agents[i].Supervised {
				c.agents[i].Status = StatusComingSoon
				c.agents[i].NotOfferedReason = "no way to run it yet"
			}
			return
		}
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

func seedAgents() []Agent {
	return []Agent{
		// --- Installed ---
		{
			ID: "bash", Name: "Bash", Description: "Shell command execution and automation",
			Category: "DevOps", Tags: []string{"shell", "bash", "terminal", "cli"},
			Icon: "🖥️", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-bash", BinaryArgs: []string{"--json-events", "--model", "Gemma-4-E4B-it-GGUF"},
		},

		// --- Available (Python agents — need API client mode) ---
		{
			ID: "chat", Name: "Chat", Description: "General conversation and Q&A",
			Category: "Conversation", Tags: []string{"chat", "general", "qa"},
			Icon: "💬", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "doc", Name: "Doc", Description: "Document analysis with RAG",
			Category: "Documents", Tags: []string{"documents", "rag", "pdf", "search"},
			Icon: "📄", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "file", Name: "File", Description: "File system navigation and operations",
			Category: "Productivity", Tags: []string{"files", "filesystem", "io"},
			Icon: "📁", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "code", Name: "Code", Description: "Code generation and editing",
			Category: "Code", Tags: []string{"code", "programming", "developer"},
			Icon: "🔧", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "blender", Name: "Blender", Description: "3D scene automation and modeling",
			Category: "Creative", Tags: []string{"3d", "blender", "modeling", "animation"},
			Icon: "🎨", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "jira", Name: "Jira", Description: "Issue tracking and project management",
			Category: "Productivity", Tags: []string{"jira", "issues", "project", "agile"},
			Icon: "🎫", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "docker", Name: "Docker", Description: "Container management and orchestration",
			Category: "DevOps", Tags: []string{"docker", "containers", "kubernetes"},
			Icon: "🐳", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "summarize", Name: "Summarize", Description: "Document and text summarization",
			Category: "Documents", Tags: []string{"summarize", "text", "tldr"},
			Icon: "📝", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			// The email agent is an HTTP sidecar the daemon supervises, not a
			// binary the TUI can spawn — it is reached through the daemon relay.
			ID: "email", Name: "Email", Description: "Email triage, drafting, and calendar",
			Category: "Productivity", Tags: []string{"email", "gmail", "calendar", "communication"},
			Icon: "📧", Version: "0.1.0", Status: StatusAvailable,
			Transport: TransportDaemon,
		},

		// --- Coming Soon ---
		{
			ID: "routing", Name: "Routing", Description: "Intelligent agent selection and orchestration",
			Category: "Infrastructure", Tags: []string{"routing", "orchestration", "multi-agent"},
			Icon: "🔀", Version: "0.1.0", Status: StatusComingSoon,
		},
		{
			ID: "browser", Name: "Browser", Description: "Web browsing and automation",
			Category: "Research", Tags: []string{"browser", "web", "scraping", "automation"},
			Icon: "🌐", Version: "0.1.0", Status: StatusComingSoon,
		},
		{
			ID: "data-analyst", Name: "Data Analyst", Description: "Data analysis and visualization",
			Category: "Data", Tags: []string{"data", "analysis", "charts", "csv", "excel"},
			Icon: "📊", Version: "0.1.0", Status: StatusComingSoon,
		},
	}
}
