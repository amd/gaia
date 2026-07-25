// Package preflight is the TUI's readiness gate: the screen that tells a user
// why an agent cannot start and what to press to fix it.
//
// It answers four generic preconditions — the daemon is up and speaks our
// contract, the agent's sidecar is running, the local model server is reachable
// at a compatible version, and the model is downloaded — plus any number of
// per-agent extra checks (for email: a mailbox that is both connected AND
// granted send). Every answer is machine-readable already; this package turns
// those answers into one row per precondition with a state, a human line, and a
// remedy that names a real command.
//
// Two rules the rest of the package exists to enforce:
//
//   - `unknown` is not `ok`. GET /v1/<agent>/init reports `compatible: null`
//     when Lemonade does not advertise a version. That is an indeterminate
//     check, not a pass, and it renders as its own state.
//   - No raw HTTP status ever reaches the user. Ladder maps a failed call to
//     {cause, remedy, where-to-look}, specific causes before generic ones.
package preflight

import (
	"fmt"
	"strings"
)

// State is a precondition's answer. Pending and Unknown are deliberately
// distinct from OK: "not checked yet" and "could not be determined" are both
// failures to prove readiness, and neither may render as a checkmark.
type State int

const (
	// StatePending — not probed yet, or gated behind an earlier failure.
	StatePending State = iota
	// StateChecking — a probe is in flight.
	StateChecking
	// StateOK — proved ready.
	StateOK
	// StateUnknown — probed, but the answer was indeterminate. NOT a pass.
	StateUnknown
	// StateFailed — proved not ready.
	StateFailed
)

// Marker is the text signal for a state. Colour is additive only: these markers
// are the primary signal so the screen reads on a monochrome terminal.
func (s State) Marker() string {
	switch s {
	case StateOK:
		return "[ok]"
	case StateFailed:
		return "[!]"
	case StateUnknown:
		return "[?]"
	case StateChecking:
		return "[..]"
	default:
		return "[ ]"
	}
}

// Word is the state as a lowercase word, for logs, snapshots, and tests.
func (s State) Word() string {
	switch s {
	case StateOK:
		return "ok"
	case StateFailed:
		return "failed"
	case StateUnknown:
		return "unknown"
	case StateChecking:
		return "checking"
	default:
		return "pending"
	}
}

// FixKind is the action a row's `f` key performs. FixNone means no fix is both
// safe and obvious from here — the row still carries a command the user can run.
type FixKind int

const (
	// FixNone — nothing safe to do from the TUI; follow the remedy command.
	FixNone FixKind = iota
	// FixStartDaemon — start-or-attach the GAIA daemon.
	FixStartDaemon
	// FixStartSidecar — ask the daemon to spawn-or-attach the agent sidecar.
	FixStartSidecar
	// FixPullModel — POST /v1/<agent>/init and stream provisioning progress.
	FixPullModel
	// FixConnectMailbox — hand off to the connector flow (Stage 4).
	FixConnectMailbox
)

// Label is the key hint shown next to `f`.
func (k FixKind) Label() string {
	switch k {
	case FixStartDaemon:
		return "start it for me"
	case FixStartSidecar:
		return "start the agent"
	case FixPullModel:
		return "download it now"
	case FixConnectMailbox:
		return "connect a mailbox"
	default:
		return ""
	}
}

// Remedy is what to do about a row that is not OK. Per the fail-loudly rule
// every remedy names what to do (Action), how (Command, when one exists), and
// where to look next (Where).
type Remedy struct {
	Action  string
	Command string
	Where   string
}

// Empty reports whether there is nothing to tell the user.
func (r Remedy) Empty() bool {
	return r.Action == "" && r.Command == "" && r.Where == ""
}

// Row is one precondition.
type Row struct {
	// Key is the stable identifier (KeyDaemon, KeySidecar, ...).
	Key string
	// Label is the human name shown in the left column.
	Label string
	// State is the answer.
	State State
	// Line is the one-line human status, e.g. "running (pid 41822)".
	Line string
	// Detail is an optional sentence of context under a failure.
	Detail string
	// Remedy is what to do about it. Empty when State is OK.
	Remedy Remedy
	// Fix is what `f` does on this row, FixNone when nothing is safe.
	Fix FixKind
	// Provider is the mailbox provider a FixConnectMailbox targets.
	Provider string
	// Raw is the probe's raw answer (JSON body or error text), shown by `d`.
	Raw string
}

// NeedsAttention reports whether the row is anything other than proved ready.
func (r Row) NeedsAttention() bool { return r.State != StateOK }

// Stable row keys. The first four are generic to any sidecar agent that
// implements GET /v1/<agent>/init; KeyMailbox is email-specific and arrives
// through Config.Extras.
const (
	KeyDaemon   = "daemon"
	KeySidecar  = "sidecar"
	KeyLemonade = "lemonade"
	KeyModel    = "model"
	KeyMailbox  = "mailbox"
)

// Report is the whole readiness answer: one row per precondition, in dependency
// order.
type Report struct {
	AgentID   string
	AgentName string
	Rows      []Row
}

// Ready reports whether every precondition proved OK. An indeterminate row is
// not ready — `compatible: null` is not a pass.
func (r Report) Ready() bool {
	if len(r.Rows) == 0 {
		return false
	}
	for _, row := range r.Rows {
		if row.State != StateOK {
			return false
		}
	}
	return true
}

// Blocked reports whether at least one precondition proved NOT ready. A report
// that is neither Ready nor Blocked has only indeterminate rows: the user may
// proceed, but the screen must say what could not be verified.
func (r Report) Blocked() bool {
	for _, row := range r.Rows {
		if row.State == StateFailed {
			return true
		}
	}
	return false
}

// OKCount is how many preconditions proved ready.
func (r Report) OKCount() int {
	n := 0
	for _, row := range r.Rows {
		if row.State == StateOK {
			n++
		}
	}
	return n
}

// Summary is the top-right status, e.g. "2 of 5 ready".
func (r Report) Summary() string {
	if r.Ready() {
		return "ready"
	}
	return fmt.Sprintf("%d of %d ready", r.OKCount(), len(r.Rows))
}

// FirstAttention is the index of the row the user should act on: the first
// FAILED row when there is one, otherwise the first row that is not OK.
//
// The order matters. An indeterminate row can sit above a real failure — a
// Lemonade that answers without advertising a version, above a model that is
// genuinely missing — and focusing the indeterminate one would put the cursor
// on the row with nothing to do.
func (r Report) FirstAttention() int {
	for i, row := range r.Rows {
		if row.State == StateFailed {
			return i
		}
	}
	for i, row := range r.Rows {
		if row.NeedsAttention() {
			return i
		}
	}
	return -1
}

// Find returns the row with the given key.
func (r Report) Find(key string) (Row, bool) {
	for _, row := range r.Rows {
		if row.Key == key {
			return row, true
		}
	}
	return Row{}, false
}

// Blocker returns the first failed row, or false when nothing failed. It is what
// an integrator logs or shows in a one-line status when preflight refuses.
func (r Report) Blocker() (Row, bool) {
	for _, row := range r.Rows {
		if row.State == StateFailed {
			return row, true
		}
	}
	return Row{}, false
}

// String renders the report as plain text — for `--debug` logs and for the
// headless callers that never mount the screen.
func (r Report) String() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s preflight: %s\n", r.AgentName, r.Summary())
	for _, row := range r.Rows {
		fmt.Fprintf(&b, "  %-4s %-20s %s\n", row.State.Marker(), row.Label, row.Line)
		if row.State == StateOK {
			continue
		}
		if row.Remedy.Action != "" {
			fmt.Fprintf(&b, "        %s\n", row.Remedy.Action)
		}
		if row.Remedy.Command != "" {
			fmt.Fprintf(&b, "        run: %s\n", row.Remedy.Command)
		}
		if row.Remedy.Where != "" {
			fmt.Fprintf(&b, "        look: %s\n", row.Remedy.Where)
		}
	}
	return b.String()
}
