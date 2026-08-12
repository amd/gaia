package catalog

import "strings"

// AgentStatus represents the lifecycle state of an agent.
type AgentStatus int

const (
	StatusInstalled  AgentStatus = iota // downloaded and ready to use
	StatusActive                        // currently in a chat session
	StatusIdle                          // used this session, back at hub
	StatusAvailable                     // in registry but not downloaded
	StatusComingSoon                    // placeholder, voteable
)

// String returns a human-readable status label.
func (s AgentStatus) String() string {
	switch s {
	case StatusInstalled:
		return "installed"
	case StatusActive:
		return "active"
	case StatusIdle:
		return "idle"
	case StatusAvailable:
		return "available"
	case StatusComingSoon:
		return "coming soon"
	default:
		return "unknown"
	}
}

// StatusDot returns the dot indicator for this status.
func (s AgentStatus) StatusDot() string {
	switch s {
	case StatusActive:
		return "●" // render green
	case StatusIdle:
		return "●" // render yellow
	case StatusInstalled:
		return "●" // render dim
	case StatusAvailable:
		return "○"
	case StatusComingSoon:
		return "◌"
	default:
		return " "
	}
}

// IsLaunchable returns true if the agent can be launched for chat.
func (s AgentStatus) IsLaunchable() bool {
	return s == StatusInstalled || s == StatusActive || s == StatusIdle
}

// Transport is how the TUI talks to an agent.
//
// The zero value is TransportSubprocess — the original stdin/stdout JSONL path,
// which every pre-existing catalog entry uses.
type Transport int

const (
	// TransportSubprocess spawns BinaryPath and trades newline-delimited JSON
	// over stdin/stdout. Used by the local C++ agents.
	TransportSubprocess Transport = iota

	// TransportDaemon streams canonical SSE events through the GAIA daemon's
	// relay (POST /v1/<id>/query). Used by the long-lived HTTP sidecar agents,
	// which the daemon starts and supervises — there is no binary to spawn.
	TransportDaemon
)

// String returns the wire name of the transport.
func (t Transport) String() string {
	switch t {
	case TransportSubprocess:
		return "subprocess"
	case TransportDaemon:
		return "daemon"
	default:
		return "unknown"
	}
}

// Agent represents a GAIA agent in the catalog.
type Agent struct {
	ID          string
	Name        string
	Description string
	Category    string
	Tags        []string
	Icon        string // emoji
	Version     string // semver, e.g. "0.1.0"
	Status      AgentStatus
	Transport   Transport
	BinaryPath  string   // e.g. "gaia-bash" (subprocess transport only)
	BinaryArgs  []string // e.g. ["--json-events"] (subprocess transport only)
	// CanonicalEvents marks a subprocess agent that writes the CANONICAL event
	// vocabulary over the pipe rather than the frozen legacy one. Only canonical
	// events carry tool narration and result previews, so an agent that emits
	// them and is parsed as legacy loses its progress reporting silently.
	CanonicalEvents bool
	Votes           int // for coming-soon agents

	// --- Agent Hub fields, populated from GET /daemon/v1/catalog ---

	// FromHub is true once this entry has been merged with a hub catalog row.
	// Only a hub-backed entry can be installed or uninstalled through the
	// daemon; everything else is a local/seed entry the daemon cannot manage.
	FromHub bool
	// Supervised means the daemon has a sidecar spec for this agent, i.e. it
	// could actually start it after installing.
	Supervised        bool
	InstalledVersion  string
	LatestVersion     string
	DownloadSizeBytes int64
	SecurityTier      string
	Author            string
	Permissions       []string
	UpdateAvailable   bool
	// NotOfferedReason explains why an entry is shown as "not out" instead of
	// installable. Empty for everything the user can act on.
	NotOfferedReason string
}

// RequiresTrust reports whether installing this agent runs code GAIA has not
// verified, so the daemon will refuse without an explicit opt-in. An entry with
// no known tier is treated as needing trust: "unknown" must never read as
// "safe".
func (a Agent) RequiresTrust() bool { return a.SecurityTier != TierVerified }

// Installable reports whether `i` can do anything with this row: the daemon
// knows how to fetch AND start it, and it is not already installed.
func (a Agent) Installable() bool {
	return a.FromHub && a.Supervised && a.Status == StatusAvailable
}

// Uninstallable reports whether this row can be removed through the daemon.
func (a Agent) Uninstallable() bool {
	return a.FromHub && a.InstalledVersion != ""
}

// Publisher is the display name of whoever published the agent.
func (a Agent) Publisher() string {
	if a.Author == "" {
		return "unknown"
	}
	return a.Author
}

// SecurityLabel is the tier as a person should read it.
func (a Agent) SecurityLabel() string {
	switch a.SecurityTier {
	case TierVerified:
		return "verified by AMD"
	case TierCommunity:
		return "community (not verified)"
	case TierExperimental:
		return "experimental (not verified)"
	case "":
		return "unknown (not verified)"
	default:
		return a.SecurityTier + " (not verified)"
	}
}

// FilterValue returns a searchable string for fuzzy matching.
// Implements the bubbles/list.Item interface.
func (a Agent) FilterValue() string {
	parts := []string{a.Name, a.Description, a.Category}
	parts = append(parts, a.Tags...)
	return strings.Join(parts, " ")
}

// Title returns the display title for list rendering.
func (a Agent) Title() string {
	return a.Icon + " " + a.Name
}
