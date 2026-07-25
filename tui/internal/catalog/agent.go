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
	Votes       int      // for coming-soon agents
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
