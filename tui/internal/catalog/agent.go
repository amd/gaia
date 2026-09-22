package catalog

// AgentStatus is whether the daemon has this agent on disk.
//
// It used to be a five-state lifecycle driving the hub's tabs and dot colours.
// Nothing browses agents any more, so what is left is the one question the
// install machinery still asks: can this be started, or does it have to be
// fetched first? Whether it is READY to start is the readiness gate's answer,
// not this field's.
type AgentStatus int

const (
	StatusInstalled AgentStatus = iota // on disk, startable
	StatusAvailable                    // in the hub index, not fetched yet
)

// String returns a human-readable status label.
func (s AgentStatus) String() string {
	switch s {
	case StatusInstalled:
		return "installed"
	case StatusAvailable:
		return "available"
	default:
		return "unknown"
	}
}

// IsLaunchable returns true if the agent is on disk.
func (s AgentStatus) IsLaunchable() bool { return s == StatusInstalled }

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
	// DevArgs are appended to BinaryArgs when the TUI runs in developer mode, so
	// one `--dev` turns on rich output here AND verbose logging in the child.
	//
	// Opt-in per agent rather than a blanket "--dev": an agent that does not know
	// the flag dies at exec on an unknown argument, which would turn a verbosity
	// switch into a launch failure. Empty means "no developer mode", the safe
	// default for every entry that has not declared one.
	DevArgs []string

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
}
