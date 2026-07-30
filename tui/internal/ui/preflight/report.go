// Package preflight is the TUI's readiness gate: the screen that tells a user
// why an agent cannot start and what to press to fix it.
//
// It answers four generic preconditions — the daemon is up and speaks our
// contract, the agent's sidecar is running, the local model server is reachable
// at a compatible version, and the model is downloaded — plus any number of
// per-agent extra checks (for email: a mailbox that is connected, granted send,
// AND proven readable). Every answer is machine-readable already; this package
// turns those answers into one row per precondition with a state, a human line,
// and a remedy that names a real command.
//
// Three rules the rest of the package exists to enforce:
//
//   - `unknown` is not `ok`. GET /v1/<agent>/init reports `compatible: null`
//     when Lemonade does not advertise a version. That is an indeterminate
//     check, not a pass, and it renders as its own state.
//   - No raw HTTP status ever reaches the user. Ladder maps a failed call to
//     {cause, remedy, where-to-look}, specific causes before generic ones.
//   - `configured` is not `working`. A row that can only read a service's own
//     record of itself has not verified anything the user will feel. Where the
//     record can disagree with reality — a stored OAuth connection reports
//     `connected: true` long after its credentials stop working — the row makes
//     the smallest real call instead, and reports `unknown` when even that
//     cannot answer. See MailboxCheck.
//
// # What an indeterminate row does, and why that is not the gate going soft
//
// Three separate questions get three separate answers, and conflating them is
// what makes a gate either dishonest or useless:
//
//   - Is it proven ready?   Report.Ready() — false for an unknown row, always.
//   - Is it proven broken?  Report.Blocked() — false for an unknown row.
//   - Does the launch stop? Depends on the row's Disposition (see Row).
//
// So an unknown row renders `[?]` and keeps Ready() false either way. Most of
// them — an unadvertised Lemonade version, two mailboxes linked so neither
// can be probed — are Disposition Notify: named on screen while the agent
// starts, launch not stopped. The alternative — demanding a keypress every
// time — was rejected deliberately for these: the condition is one the user
// cannot fix (their Lemonade build simply does not report a version), so the
// prompt would fire on every single launch forever. A prompt that always
// fires and never means anything is dismissed reflexively, and it devalues
// every other prompt in the product. The signal is kept; the ritual is not.
//
// A handful of unknown rows are Disposition Halt instead — a model loaded
// into a context window smaller than the profile pins, where a document-sized
// request comes back context_length_exceeded. Unlike the Notify cases, this
// one is consequential and not something re-checking clears on its own, so it
// holds for a person rather than proceeding silently. See internal/ui/status
// for how a Halt row reaches the rest of the app.
//
// A row that is genuinely broken (StateFailed) still blocks via Report.Blocked
// unless it is one the agent itself can repair once the conversation starts
// (Report.OfferableDespiteFailure) — that one keystroke opens the door to the
// repair instead of a second gate in front of it.
package preflight

import (
	"fmt"
	"strings"

	"github.com/amd/gaia/tui/internal/ui/status"
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
	// Disposition is what the checklist does when this row is not OK — Halt
	// or Notify. Set by the check that produces the row, because the check
	// is what knows whether being unverified will actually bite the user.
	// DispositionUnset on a StateFailed or StateUnknown row is a bug — see
	// TestEveryNonOKRowDeclaresADisposition.
	Disposition status.Disposition
}

// NeedsAttention reports whether the row is anything other than proved ready.
func (r Row) NeedsAttention() bool { return r.State != StateOK }

// levelFor maps a row's State onto the status package's Level. Pending and
// Checking are transient placeholders, not a resolved answer, so both map to
// LevelUnset — consistent with LevelUnset never being a decided disposition
// either.
func levelFor(s State) status.Level {
	switch s {
	case StateOK:
		return status.LevelOK
	case StateUnknown:
		return status.LevelUnknown
	case StateFailed:
		return status.LevelFailed
	default:
		return status.LevelUnset
	}
}

// Outcome converts the row into the status package's shared vocabulary, for
// anything listening for a consequential state (see internal/ui/status and
// RootModel's listener). Summary is sanitized through status.New — Line and
// Detail can carry upstream server text verbatim (checkInit assigns
// body.hint() straight to Detail, and Ladder builds causes with %s over
// upstream/transport text), and this is the boundary where that text stops
// being trusted, even though the row itself still carries it unsanitized for
// the existing on-screen remedy rendering.
func (r Row) Outcome() status.Outcome {
	summary := r.Detail
	if summary == "" {
		summary = r.Line
	}
	return status.New(r.Key, r.Label, levelFor(r.State), r.Disposition, summary)
}

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

// agentRepairable names preconditions the agent itself can fix once the
// conversation starts. Central map rather than a per-row flag so a newly added
// mailbox state cannot forget to set it.
var agentRepairable = map[string]bool{KeyMailbox: true}

// OfferableDespiteFailure reports whether the user may CHOOSE to launch over the
// failures found — true when every failed row is one the agent repairs in the
// conversation. Deliberately separate from Blocked: a launch is never automatic
// over a proven failure, but refusing the choice would hide the mailbox
// onboarding behind the gate that detects it.
func (r Report) OfferableDespiteFailure() bool {
	failed := false
	for _, row := range r.Rows {
		if row.State != StateFailed {
			continue
		}
		if !agentRepairable[row.Key] {
			return false
		}
		failed = true
	}
	return failed
}

// needsHalt is shared by HasHalt and HaltingRows so the two can never
// disagree about which rows count. Notify is the value a row must opt INTO
// to auto-proceed — anything else, including a forgotten DispositionUnset,
// holds. Inverted on purpose: the alternative (require == DispositionHalt)
// reproduces the exact bug this issue exists to fix, one field at a time — a
// row nobody assigned a Disposition to would silently proceed instead of
// loudly halting.
func (r Row) needsHalt() bool {
	return r.State != StateOK && r.Disposition != status.DispositionNotify
}

// HasHalt reports whether any row in the report needsHalt.
func (r Report) HasHalt() bool {
	for _, row := range r.Rows {
		if row.needsHalt() {
			return true
		}
	}
	return false
}

// HaltingRows returns the rows that needsHalt, for building the Outcomes a
// listener sees. Emission is level-triggered: Check resolves every row in
// one pass, so this is called once per report, not streamed per row.
func (r Report) HaltingRows() []Row {
	var out []Row
	for _, row := range r.Rows {
		if row.needsHalt() {
			out = append(out, row)
		}
	}
	return out
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
