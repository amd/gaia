package ui

import (
	"context"
	"fmt"
	"io"
	"strings"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// OneShotResult is the outcome of one non-interactive turn. It mirrors
// gaia.daemon.agent_query.QueryOutcome so the Go and Python thin clients report
// the same thing.
type OneShotResult struct {
	// ExitCode is 0 on a terminal `final`, 1 on a terminal `error` or a stream
	// that ended without either.
	ExitCode int
	// TerminalType is "final", "error", or "" when no terminal event arrived.
	TerminalType string
	Answer       string
	ErrorDetail  string
}

// RunOneShot drives a single turn and renders it to plain streams — no alt
// screen, no TTY required.
//
// The answer goes to out and progress to errW, so `... --query X > answer.txt`
// captures exactly the answer and nothing else. This is the same split the
// `gaia <agent>` CLI uses, and it is what makes the transport testable from a
// script and from CI.
func RunOneShot(
	ctx context.Context,
	c client.AgentClient,
	query string,
	out, errW io.Writer,
) OneShotResult {
	ch, err := c.Send(ctx, query)
	if err != nil {
		fmt.Fprintf(errW, "❌ %v\n", err)
		return OneShotResult{ExitCode: 1, ErrorDetail: err.Error()}
	}

	var (
		res       OneShotResult
		streamed  strings.Builder
		sawTokens bool
	)

	for evt := range ch {
		switch e := evt.(type) {
		case event.CanonicalStatusEvent:
			if msg := strings.TrimSpace(e.Message); msg != "" {
				fmt.Fprintf(errW, "  … %s\n", msg)
			}

		case event.CanonicalTokenEvent:
			if e.Delta == "" {
				continue
			}
			sawTokens = true
			streamed.WriteString(e.Delta)
			fmt.Fprint(out, e.Delta)

		case event.CanonicalToolCallEvent:
			fmt.Fprintf(errW, "  🔧 %s\n", e.Tool)

		case event.CanonicalToolResultEvent:
			if e.Render != "" {
				fmt.Fprintf(errW, "  ✓ %s (%s)\n", e.Tool, e.Render)
			} else {
				fmt.Fprintf(errW, "  ✓ %s\n", e.Tool)
			}

		case event.CanonicalNeedsConfirmationEvent:
			line := "  ⚠️  confirmation needed: " + e.Action
			if summary := strings.TrimSpace(e.Summary); summary != "" {
				line += " — " + summary
			}
			fmt.Fprintln(errW, line)

		case event.CanonicalFinalEvent:
			res.TerminalType = event.CanonicalTypeFinal
			// `answer` is authoritative; the streamed tokens are the fallback for
			// a sidecar that streams and then closes with an empty final.
			res.Answer = e.Answer
			if res.Answer == "" {
				res.Answer = streamed.String()
			}
			if sawTokens {
				// The tokens already printed the answer live — just end the line.
				fmt.Fprintln(out)
			} else if res.Answer != "" {
				fmt.Fprintln(out, res.Answer)
			}
			if usage := event.CanonicalUsageOf(e); usage.Steps > 0 || usage.ToolsUsed > 0 {
				fmt.Fprintf(errW, "  ℹ️  %d steps, %d tools\n", usage.Steps, usage.ToolsUsed)
			}

		case event.CanonicalErrorEvent:
			res.TerminalType = event.CanonicalTypeError
			res.ErrorDetail = e.Detail
			if sawTokens {
				fmt.Fprintln(out)
			}
			fmt.Fprintf(errW, "❌ %s\n", e.Detail)

		case event.CanonicalUnsupportedEvent:
			// Contract §7: visible, never dropped.
			fmt.Fprintf(errW, "  [unsupported event %q]\n", e.EventType)

		case event.CanonicalMalformedEvent:
			fmt.Fprintf(errW, "  [unreadable event skipped: %s]\n", e.Reason)

		// The legacy in-process vocabulary, so this renderer also works when
		// pointed at a subprocess agent.
		case event.AnswerEvent:
			res.TerminalType = event.CanonicalTypeFinal
			res.Answer = e.Content
			fmt.Fprintln(out, e.Content)
		case event.AgentErrorEvent:
			res.TerminalType = event.CanonicalTypeError
			res.ErrorDetail = e.Content
			fmt.Fprintf(errW, "❌ %s\n", e.Content)
		case event.ToolStartEvent:
			fmt.Fprintf(errW, "  🔧 %s\n", e.Tool)
		case event.StatusEvent:
			if msg := strings.TrimSpace(e.Message); msg != "" {
				fmt.Fprintf(errW, "  … %s\n", msg)
			}
		case event.ChunkEvent:
			sawTokens = true
			streamed.WriteString(e.Content)
			fmt.Fprint(out, e.Content)

		default:
			fmt.Fprintf(errW, "  [unhandled event %T]\n", evt)
		}
	}

	if res.TerminalType == "" {
		// Exactly one terminal event is mandatory; a stream that ends without one
		// is a failure, reported loudly rather than as an empty success.
		res.ErrorDetail = fmt.Sprintf(
			"the %q query stream ended without a terminal final/error event", query)
		fmt.Fprintf(errW, "❌ %s\n", res.ErrorDetail)
	}
	if res.TerminalType != event.CanonicalTypeFinal {
		res.ExitCode = 1
	}
	return res
}
