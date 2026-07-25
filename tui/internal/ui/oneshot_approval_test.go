package ui

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// Canonical-only by nature: `needs_confirmation` exists solely in the canonical
// vocabulary (the subprocess parser rejects the type outright), so these cases
// have no mock-agent equivalent and are driven through the event stream here.
//
// A confirmation gate is a security control deliberately withholding a
// destructive action, and the run ends on `final` with the agent explaining it
// declined. That satisfied every existing exit-code rule — no tool failed, the
// terminal event was a success — so
// `gaia tui run email --query "quarantine that phishing message" && echo done`
// printed `done` while the message sat untouched.
func TestAWithheldActionDoesNotExitZero(t *testing.T) {
	res, out, errW := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{
			Type: "needs_confirmation", Action: "quarantine_phishing_message",
			Summary: "Quarantine the message from postmaster@example.com",
		},
		event.CanonicalFinalEvent{
			Type:   "final",
			Answer: "Quarantining this message needs your explicit confirmation. Nothing has been done.",
		},
	)

	if res.ExitCode == 0 {
		t.Fatal("a turn that performed nothing exited 0")
	}
	if res.ExitCode != ExitApprovalRequired {
		t.Errorf("exit = %d, want %d (approval required)", res.ExitCode, ExitApprovalRequired)
	}
	if len(res.WithheldActions) != 1 || res.WithheldActions[0] != "quarantine_phishing_message" {
		t.Errorf("WithheldActions = %v, want the gated action", res.WithheldActions)
	}
	if !strings.Contains(errW, "NOT") || !strings.Contains(errW, "Nothing changed") {
		t.Errorf("stderr does not say the action did not happen:\n%s", errW)
	}
	// The agent's own explanation is still the answer.
	if !strings.Contains(out, "explicit confirmation") {
		t.Errorf("the answer was dropped from stdout:\n%s", out)
	}
}

// The code is distinct from a failure on purpose: nothing broke, the run
// stopped on purpose. A script can route one to an approval flow and the other
// to an alert.
func TestApprovalRequiredIsDistinctFromFailure(t *testing.T) {
	approval, _, _ := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_now"},
		event.CanonicalFinalEvent{Type: "final", Answer: "not sent"},
	)
	failure, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "send_now", Data: okEnvelope(false, "SMTP refused"),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "could not send"},
	)

	if approval.ExitCode == failure.ExitCode {
		t.Fatalf("approval and failure share exit %d; a script cannot tell them apart",
			approval.ExitCode)
	}
	if failure.ExitCode != 1 {
		t.Errorf("a failed tool exits %d, want 1", failure.ExitCode)
	}
}

// A gate the run then went on to satisfy is not withheld — the tool ran, so
// approval came from somewhere.
func TestAGateTheRunSatisfiesIsNotReportedAsWithheld(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_draft"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "send_draft", Data: okEnvelope(true, `"sent": 1`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "Sent."},
	)

	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0: the gated action ran\n%s", res.ExitCode, errW)
	}
	if len(res.WithheldActions) != 0 {
		t.Errorf("WithheldActions = %v, want none", res.WithheldActions)
	}
}

// A gated action that ran and FAILED is a failure, not a withheld action: it
// was approved, attempted, and broke. The failure verdict wins.
func TestAGatedActionThatRanAndFailedIsAFailure(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_draft"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "send_draft", Data: okEnvelope(false, "SMTP refused"),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "could not send"},
	)

	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1: the action ran and failed", res.ExitCode)
	}
	if len(res.WithheldActions) != 0 {
		t.Errorf("WithheldActions = %v, want none — it was not withheld", res.WithheldActions)
	}
}

// When both happen, the failure decides the code — something broke, which is
// the more urgent of the two — but the withheld action is still reported.
func TestAFailureOutranksAGateButBothAreReported(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox", Data: okEnvelope(false, "CONNECTOR_ERROR"),
		},
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "quarantine_phishing_message"},
		event.CanonicalFinalEvent{Type: "final", Answer: "I could not do either."},
	)

	if res.ExitCode != 1 {
		t.Errorf("exit = %d, want 1: a real failure outranks a gate", res.ExitCode)
	}
	if !strings.Contains(errW, "pre_scan_inbox failed") {
		t.Errorf("the failure was not reported:\n%s", errW)
	}
	if !strings.Contains(errW, "quarantine_phishing_message") {
		t.Errorf("the withheld action was not reported:\n%s", errW)
	}
}

// Under the resume model the agent sends a confirm_url. When there is one, name
// it; when there is none, do not invent a way to approve — the interactive chat
// cannot answer a gate either.
func TestAConfirmURLIsSurfacedWhenTheAgentSendsOne(t *testing.T) {
	const url = "http://127.0.0.1:8765/v1/email/confirm/abc123"
	_, _, withURL := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{
			Type: "needs_confirmation", Action: "send_now", ConfirmURL: url,
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "not sent"},
	)
	if !strings.Contains(withURL, url) {
		t.Errorf("the approval link was dropped:\n%s", withURL)
	}

	_, _, noURL := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_now"},
		event.CanonicalFinalEvent{Type: "final", Answer: "not sent"},
	)
	if strings.Contains(noURL, "approve send_now at") {
		t.Errorf("an approval link was invented where the agent sent none:\n%s", noURL)
	}
}

// Every gate is named, not just the first — a run can stop at several.
func TestEveryWithheldActionIsNamed(t *testing.T) {
	_, _, errW := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_now"},
		event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "archive_message"},
		event.CanonicalFinalEvent{Type: "final", Answer: "neither was done"},
	)
	for _, action := range []string{"send_now", "archive_message"} {
		if !strings.Contains(errW, action) {
			t.Errorf("%q was not reported:\n%s", action, errW)
		}
	}
}
