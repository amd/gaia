// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// "Where should chat run?" is a first-run step of its own, ahead of every
// download: which models setup fetches depends on the answer, and a user who
// never sees the question gets the multi-GB local download by default.

const (
	chatRowLabel   = "Chat"
	chooseChatStep = "choose where chat runs"
	providersDocs  = "https://amd-gaia.ai/docs/guides/ai-providers"
)

// ChatOption is one answer to "where should chat run?".
type ChatOption struct {
	// Provider is "local" or the Lemonade cloud provider's name.
	Provider string
	Label    string
	// TradeOff is one plain line on privacy, cost and speed.
	TradeOff string
	// Needs is what the choice takes before the first message: a download and
	// whether it fits, or a key.
	Needs string
	// Unavailable is why the choice cannot be made right now; "" when it can.
	Unavailable string
}

// ChatChosenMsg tells the host where the user chose to run chat, so a gate it
// rebuilds later — after the provider panel, say — keeps the choice.
type ChatChosenMsg struct {
	AgentID  string
	Provider string
}

// ConnectProviderMsg asks the host to open the provider panel on Provider's key
// form. The gate does not own that form: it is the same one `p` and /provider
// open, and a second copy here would fork it.
type ConnectProviderMsg struct {
	AgentID  string
	Provider string
}

// chatChooser is a runner that can be told where chat runs.
type chatChooser interface {
	WithChatProvider(provider string) Runner
}

// chatOptions lists every place chat can run, Local first. localSize is what
// the local option downloads ("" when unknown) and localFit says whether its
// model fits this PC.
func chatOptions(localSize, localFit string) []ChatOption {
	if localSize == "" {
		localSize = gaiainit.ProfileSize
	}
	out := make([]ChatOption, 0, len(lemonade.ChatProviders))
	for _, provider := range lemonade.ChatProviders {
		out = append(out, chatOption(provider, localSize, localFit))
	}
	return out
}

func chatOption(provider, localSize, localFit string) ChatOption {
	o := ChatOption{Provider: provider, Label: lemonade.Label(provider)}
	switch provider {
	case "local":
		o.TradeOff = "Private and free: nothing leaves this PC. Speed depends on this PC."
		o.Needs = strings.TrimSpace("Downloads " + localSize + " of models. " + localFit)
	case "fireworks":
		o.TradeOff = "Fast on any PC. Conversations are sent to Fireworks AI, and usage is billed to your key."
		o.Needs = "Needs a Fireworks AI API key. Downloads under 1 GB."
	case "amd":
		o.TradeOff = "Fast on any PC. Conversations are sent to your organisation's AMD LLM Gateway."
		o.Needs = "Needs your gateway key (and its URL). Downloads under 1 GB."
	default:
		o.TradeOff = "Conversations are sent to " + o.Label + "."
		o.Needs = "Needs its key. Downloads under 1 GB."
	}
	return o
}

// probeChatGateway is a var so tests reach no network.
var probeChatGateway = lemonade.ProbeGatewayFrom

// withGatewayReach marks the AMD gateway unavailable when its host does not
// answer, so the choice says "no network" before anyone picks it.
func withGatewayReach(ctx context.Context, options []ChatOption) []ChatOption {
	for i := range options {
		if options[i].Provider != "amd" {
			continue
		}
		var u *lemonade.UnreachableError
		// The first run has no gateway set up yet, so the default is the one asked about.
		if err := probeChatGateway(ctx, "", ""); errors.As(err, &u) {
			options[i].Unavailable = u.Brief()
		}
	}
	return options
}

// withChatStep puts the choice in front of the first step still to do.
func withChatStep(rep Report, options []ChatOption) Report {
	steps := rep.Steps()
	if len(steps) == 0 {
		return rep
	}
	at := steps[0]
	row := Row{
		Key: KeyChat, Label: chatRowLabel, State: StateFailed, FirstRun: true,
		Disposition: status.DispositionHalt, Fix: FixChooseChat,
		Line: "not chosen yet", Step: chooseChatStep,
		Detail: "On this PC, or with a cloud provider. The answer decides what the last step downloads.",
		Remedy: Remedy{Action: "Choose Local, Fireworks AI or AMD LLM Gateway.", Where: providersDocs},
	}
	rows := make([]Row, 0, len(rep.Rows)+1)
	rows = append(rows, rep.Rows[:at]...)
	rows = append(rows, row)
	rep.Rows = append(rows, rep.Rows[at:]...)
	rep.ChatOptions = options
	return rep
}

// connectStep is the model row's step when chat runs on a cloud provider that
// is not connected yet.
func connectStep(provider string) string {
	return "connect " + lemonade.Label(provider) + ", then a <1 GB download"
}

// connectProviderRow is the model row for a cloud provider chosen before any of
// its models: it is connected first, then setup downloads only the embedder.
func connectProviderRow(provider string) Row {
	label := lemonade.Label(provider)
	return Row{
		Key: KeyModel, State: StateFailed, FirstRun: true,
		Disposition: status.DispositionHalt, Fix: FixConnectProvider, Provider: provider,
		Line: "not connected yet", Step: connectStep(provider),
		Detail: "Paste your " + label + " key and pick a model. Setup then downloads only the " +
			"embedding model, for document search and memory.",
		Remedy: Remedy{Action: "Press p, choose " + label + ", and connect it.", Where: providersDocs},
	}
}

// readCapacity asks Lemonade what this PC can hold. A var so tests need no server.
var readCapacity = func(ctx context.Context) (lemonade.Capacity, error) {
	ctx, cancel := context.WithTimeout(ctx, lemonadeProbeTimeout)
	defer cancel()
	return lemonade.New("").Capacity(ctx)
}

// localFit says whether the local chat model fits this PC, by the same rule the
// provider panel and `gaia init` use. Before Lemonade is installed nothing can
// measure the PC, and setup then picks the model that fits.
func (l localRunner) localFit(ctx context.Context, rep Report, models []gaiainit.Model) string {
	const later = "Setup picks the model that fits this PC's memory."
	if row, ok := rep.Find(KeyLemonade); !ok || row.State != StateOK {
		return later
	}
	capacity, err := readCapacity(ctx)
	if err != nil {
		return later
	}
	chat, ok := gaiainit.Status{Models: models}.Chat()
	return fitLine(capacity, chat, ok)
}

func fitLine(c lemonade.Capacity, chat gaiainit.Model, known bool) string {
	have := fmt.Sprintf("this PC has %.0f GB for models (%s)", c.MemoryGB, c.MemorySource)
	if !known || chat.SizeGB == nil {
		return "Setup picks the model that fits; " + have + "."
	}
	kv := 0.0
	for _, r := range lemonade.RecommendedFor("local") {
		if r.Matches(chat.ID) {
			kv = r.KVCacheGB()
		}
	}
	if fits, why := c.Fit(*chat.SizeGB, kv); !fits {
		return chat.ID + " will not fit: " + why + "."
	}
	return chat.ID + " fits: " + have + "."
}

// --- the choice on screen ---------------------------------------------------

func (m Model) chatOptions() []ChatOption {
	if len(m.rep.ChatOptions) > 0 {
		return m.rep.ChatOptions
	}
	return chatOptions("", "")
}

// Choosing reports whether the screen is asking where chat runs.
func (m Model) Choosing() bool { return m.choosing }

func (m Model) openChooser() Model {
	m.choosing = true
	m.details = false
	if n := len(m.chatOptions()); m.choice >= n {
		m.choice = 0
	}
	return m
}

func (m Model) handleChooserKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	n := len(m.chatOptions())
	switch key := msg.String(); key {
	case "esc", "q":
		m.choosing = false
		return m, nil
	case "up", "k", "shift+tab":
		m.choice = (m.choice + n - 1) % n
	case "down", "j", "tab":
		m.choice = (m.choice + 1) % n
	case "enter":
		return m.chooseChat()
	case "r":
		m.choosing = false
		return m.handleKey(msg)
	default:
		if len(key) == 1 && key[0] >= '1' && int(key[0]-'0') <= n {
			m.choice = int(key[0] - '1')
		}
	}
	return m, nil
}

// chooseChat records the choice and re-checks: the step it answered is done,
// and what the remaining steps download now follows from it.
func (m Model) chooseChat() (tea.Model, tea.Cmd) {
	option := m.chatOptions()[m.choice]
	if option.Unavailable != "" {
		m.note = option.Unavailable + ". Connect to it, then press r to check again."
		return m, nil
	}
	chooser, ok := m.r.(chatChooser)
	if !ok {
		m.choosing = false
		m.note = "This screen cannot record where chat runs (the " + m.r.Label() +
			" check has no such step). Press p to choose a provider instead."
		return m, nil
	}
	m.choosing = false
	m.r = chooser.WithChatProvider(option.Provider)
	m.stepsBefore = m.rep.StepCount()
	m.note = "Chat runs on " + option.Label + ". Re-checking…"
	m.phase = phaseChecking
	agentID, provider := m.cfg.AgentID, option.Provider
	return m, tea.Batch(m.checkCmd(), m.spin.Tick, func() tea.Msg {
		return ChatChosenMsg{AgentID: agentID, Provider: provider}
	})
}

// chooserLines is the body while the question is up. Every option keeps a text
// marker and a number, so the screen reads the same without colour and to a
// screen reader; when it does not fit, the blank lines go first, then the
// detail of the options not selected — their names always stay.
func (m Model) chooserLines(w, budget int) []string {
	options := m.chatOptions()
	textW := w - 2*indentW - 7
	// tight: 0 is everything, 1 drops blank lines and what the unselected
	// options need, 2 also drops their trade-off.
	build := func(tight int) []string {
		title := "Where should chat run?"
		if n := m.rep.StepNumber(m.rowIndex(KeyChat)); n > 0 {
			title = stepText(n, m.rep.StepCount(), "where should chat run?")
		}
		out := []string{
			pad(labelStyle.Render(title)),
			pad(dimStyle.Render(truncate("Change it any time: p on this screen, /provider in chat.", w-2*indentW))),
		}
		if tight == 0 {
			out = append(out, "")
		}
		for i, o := range options {
			selected := i == m.choice
			cursor, style := "  ", labelStyle
			if selected {
				cursor, style = "> ", keyStyle
			}
			detail := []string{o.TradeOff, o.Needs}
			if o.Unavailable != "" {
				style = dimStyle
				detail = []string{o.Unavailable, o.TradeOff}
			}
			out = append(out, pad(cursor+style.Render(fmt.Sprintf("%d. %s", i+1, o.Label))))
			if !selected {
				detail = detail[:max(0, 2-tight)]
			}
			for _, d := range detail {
				for _, l := range wrap(d, textW) {
					out = append(out, pad("     "+dimStyle.Render(l)))
				}
			}
			if tight == 0 {
				out = append(out, "")
			}
		}
		if m.note != "" {
			out = append(out, indentAll(wrap(m.note, w-2*indentW), indentW)...)
		}
		return out
	}
	lines := build(0)
	for tight := 1; tight <= 2 && len(lines) > budget; tight++ {
		lines = build(tight)
	}
	return lines
}
