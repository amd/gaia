// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package hybridapi is the TUI's client for the daemon's hybrid settings
// routes. The backend owns every rule: presets, validation, which provider
// offers a model, and template availability. This package only carries its
// answers, so the TUI never re-decides them.
package hybridapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Loose is a saved value shown as the backend returned it. A hand-broken
// config.json can hold any JSON where a string belongs, and the screen must
// still show it so the user can fix it.
type Loose string

// UnmarshalJSON takes a string as is, null as "", and anything else as its JSON text.
func (l *Loose) UnmarshalJSON(raw []byte) error {
	raw = bytes.TrimSpace(raw)
	if string(raw) == "null" {
		*l = ""
		return nil
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		*l = Loose(s)
		return nil
	}
	*l = Loose(raw)
	return nil
}

// Rule says when one kind of request may run on the cheap model.
type Rule struct {
	CheapMaxDifficulty int  `json:"cheap_max_difficulty"`
	AllowHighStakes    bool `json:"allow_high_stakes"`
}

// Rules maps each request kind to its rule.
type Rules map[string]Rule

// Clone copies the table so an edit cannot reach the one it came from.
func (r Rules) Clone() Rules {
	if r == nil {
		return nil
	}
	out := make(Rules, len(r))
	for k, v := range r {
		out[k] = v
	}
	return out
}

// Equal reports whether both tables hold the same rules.
func (r Rules) Equal(o Rules) bool {
	if len(r) != len(o) {
		return false
	}
	for k, v := range r {
		if w, ok := o[k]; !ok || w != v {
			return false
		}
	}
	return true
}

// Model is one model a provider set up here offers, with the backend's
// verdict on whether hybrid can use it.
type Model struct {
	ID          string  `json:"id"`
	DisplayName string  `json:"display_name"`
	Provider    string  `json:"provider"`
	Available   bool    `json:"available"`
	Reason      *string `json:"reason"`
}

// Entry is hybrid's row in a model picker, named from the configured pair.
type Entry struct {
	ID          string  `json:"id"`
	DisplayName string  `json:"display_name"`
	Remote      *bool   `json:"remote"`
	Available   bool    `json:"available"`
	Reason      *string `json:"reason"`
	Shown       bool    `json:"shown"`
}

// Label is the entry's name with where it runs.
func (e Entry) Label() string {
	switch {
	case e.Remote == nil:
		return e.DisplayName
	case *e.Remote:
		return e.DisplayName + " (cloud)"
	}
	return e.DisplayName + " (local)"
}

// Why is the backend's reason the entry can't be chosen, or "".
func (e Entry) Why() string {
	if e.Reason != nil && strings.TrimSpace(*e.Reason) != "" {
		return *e.Reason
	}
	if !e.Available {
		return "no hybrid pairing can be used here yet; set one up in /hybrid"
	}
	return ""
}

// Settings is GET /api/hybrid/settings, and what every change answers with.
type Settings struct {
	CheapModel        Loose `json:"cheap_model"`
	StrongModel       Loose `json:"strong_model"`
	ClassifierModel   Loose `json:"classifier_model"`
	ClassifierTimeout Loose `json:"classifier_timeout"`
	Policy            Loose `json:"policy"`
	// Rules is the effective table, raw: a hand-broken one is still shown as saved.
	Rules json.RawMessage `json:"rules"`
	// Presets are the preset policies' tables, in the backend's order.
	Presets        json.RawMessage `json:"presets"`
	Kinds          []string        `json:"kinds"`
	Models         []Model         `json:"models"`
	ModelsError    *string         `json:"models_error"`
	Measured       bool            `json:"measured"`
	MeasuredNote   *string         `json:"measured_note"`
	ClassifierNote *string         `json:"classifier_note"`
	Entry          *Entry          `json:"entry"`
	IsActive       *bool           `json:"is_active"`
	ConfigError    *string         `json:"config_error"`
}

// Preset is one named policy and its table.
type Preset struct {
	Name  string
	Rules Rules
}

// PresetList is Presets in the backend's order.
func (s Settings) PresetList() ([]Preset, error) {
	if len(bytes.TrimSpace(s.Presets)) == 0 || string(bytes.TrimSpace(s.Presets)) == "null" {
		return nil, nil
	}
	dec := json.NewDecoder(bytes.NewReader(s.Presets))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return nil, fmt.Errorf("the backend's presets aren't an object; update GAIA so this app and its background service match")
	}
	var out []Preset
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, fmt.Errorf("the backend's presets can't be read: %w", err)
		}
		name, _ := tok.(string)
		var rules Rules
		if err := dec.Decode(&rules); err != nil {
			return nil, fmt.Errorf("the backend's %q preset can't be read: %w", name, err)
		}
		out = append(out, Preset{Name: name, Rules: rules})
	}
	return out, nil
}

// RuleTable is Rules decoded; an error when the saved table isn't one.
func (s Settings) RuleTable() (Rules, error) {
	raw := bytes.TrimSpace(s.Rules)
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var r Rules
	if err := json.Unmarshal(raw, &r); err != nil {
		return nil, fmt.Errorf("the saved rules aren't a table of rules (%s)", raw)
	}
	return r, nil
}

// Template is one built-in pairing, resolved on this PC by the backend.
type Template struct {
	ID              string  `json:"id"`
	Name            string  `json:"name"`
	Description     string  `json:"description"`
	CheapModel      *string `json:"cheap_model"`
	StrongModel     *string `json:"strong_model"`
	ClassifierModel *string `json:"classifier_model"`
	Policy          string  `json:"policy"`
	Available       bool    `json:"available"`
	Reason          *string `json:"reason"`
	Measured        bool    `json:"measured"`
}

// Why is the backend's reason the template can't be used, or "".
func (t Template) Why() string {
	if t.Reason != nil {
		return *t.Reason
	}
	return ""
}

// Stats is GET /api/hybrid/stats: how hybrid turns were routed and what they cost.
type Stats struct {
	Since        string `json:"since"`
	Turns        int    `json:"turns"`
	CheapTurns   int    `json:"cheap_turns"`
	StrongTurns  int    `json:"strong_turns"`
	FailedClosed int    `json:"failed_closed"`
	// Cost and CostIfAllStrong are nil when a turn's price is unknown, never $0.
	Cost            *float64 `json:"cost"`
	CostIfAllStrong *float64 `json:"cost_if_all_strong"`
	SavingPct       *float64 `json:"saving_pct"`
	// UnpricedTurns ran on a model with an unknown price.
	UnpricedTurns int `json:"unpriced_turns"`
	// Scope is which chats the record covers: agent_ui_chats or all.
	Scope string `json:"scope"`
	// ScopeLabel is Scope in the backend's own words, shown verbatim.
	ScopeLabel string `json:"scope_label"`
}

// Scopes the stats can cover.
const (
	ScopeAgentUIChats = "agent_ui_chats"
	ScopeAll          = "all"
)

func (s Stats) check() error {
	switch s.Scope {
	case ScopeAgentUIChats, ScopeAll:
		return nil
	}
	return fmt.Errorf("the routing stats name a scope this app doesn't know (%q); "+
		"update GAIA so this app and its background service match", s.Scope)
}

// Line is the stats in one sentence.
func (s Stats) Line() string {
	if s.Turns == 0 {
		return "No hybrid turns recorded yet (" + s.scopeLabel() + ")."
	}
	line := fmt.Sprintf("%d %s: %d on the cheap model, %d on the strong",
		s.Turns, plural(s.Turns, "turn"), s.CheapTurns, s.StrongTurns)
	if s.FailedClosed > 0 {
		line += fmt.Sprintf(" (%d unclassified)", s.FailedClosed)
	}
	line += " · " + spent(s.Cost) + ", " + allStrong(s.CostIfAllStrong)
	if s.SavingPct != nil && *s.SavingPct > 0 {
		line += fmt.Sprintf(" · %.0f%% saved", *s.SavingPct)
	}
	if s.UnpricedTurns > 0 {
		line += fmt.Sprintf(" · %d %s at an unknown price", s.UnpricedTurns, plural(s.UnpricedTurns, "turn"))
	}
	if s.Since != "" {
		line = "since " + day(s.Since) + ": " + line
	}
	return s.scopeLabel() + ", " + line
}

func (s Stats) scopeLabel() string {
	if label := strings.TrimSpace(s.ScopeLabel); label != "" {
		return strings.TrimSuffix(label, ".")
	}
	if s.Scope == ScopeAll {
		return "All chats"
	}
	return "Agent UI chats only"
}

func spent(c *float64) string {
	if c == nil {
		return "cost unknown"
	}
	return usd(*c) + " spent"
}

func allStrong(c *float64) string {
	if c == nil {
		return "unknown if all strong"
	}
	return usd(*c) + " if all strong"
}

// usd shows a cost to the cent, or to a hundredth of a cent below one cent,
// so a small spend never reads as $0.00 beside a saving.
func usd(c float64) string {
	if c > 0 && c < 0.01 {
		return fmt.Sprintf("$%.4f", c)
	}
	return fmt.Sprintf("$%.2f", c)
}

// day is an ISO timestamp as a date, "16 Nov 2026"; anything else is shown as sent.
func day(ts string) string {
	if t, err := time.Parse(time.RFC3339Nano, ts); err == nil {
		return t.Local().Format("2 Jan 2006")
	}
	if t, err := time.Parse("2006-01-02", ts); err == nil {
		return t.Format("2 Jan 2006")
	}
	return ts
}

func plural(n int, word string) string {
	if n == 1 {
		return word
	}
	return word + "s"
}
