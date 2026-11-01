// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"sort"

	"github.com/amd/gaia/tui/internal/setupapi"
)

// rowEntry is one catalog model as the checklist shows it. Built from the
// backend's plan; nothing here decides fit, size or what is recommended.
type rowEntry struct {
	id, name    string
	chatable    bool // can be the chat model
	vision      bool
	embedding   bool
	npu         bool
	recommended bool
	planned     bool
	selected    bool // planned and not skipped, per the backend
	skipped     bool
	downloaded  bool
	partial     bool
	partialGB   float64  // already on disk from an interrupted pull
	leftGB      *float64 // still to download; nil when unknown
	fullGB      float64
	fits        *bool
	needGB      *float64
	haveGB      *float64
	// installed puts the row in the "already in Lemonade" group: on disk, not
	// one of setup's own models. It can be the chat model; it has no tick.
	installed bool
}

func entryFrom(e setupapi.CatalogEntry) rowEntry {
	r := rowEntry{
		id:          e.ID,
		name:        shortName(e.ID),
		chatable:    e.ChatChoice && !e.HasRole(setupapi.RoleEmbedding),
		vision:      e.HasRole(setupapi.RoleVision),
		embedding:   e.HasRole(setupapi.RoleEmbedding),
		npu:         e.RunsOnNPU(),
		recommended: e.Recommended,
		planned:     e.Planned,
		selected:    e.Selected,
		skipped:     e.Skipped,
		downloaded:  e.IsDownloaded(),
		partial:     e.Partial,
		partialGB:   e.PartialGB,
		leftGB:      e.SizeGB,
		fits:        e.Fits,
		needGB:      e.NeedGB,
		haveGB:      e.CapacityGB,
	}
	if e.FullSizeGB != nil {
		r.fullGB = *e.FullSizeGB
	}
	// The vision and documents models stay in the main list even when setup
	// no longer plans them (a saved chat model replaces Gemma in the plan):
	// they are what those features load by name.
	r.installed = r.downloaded && !r.planned && !r.vision && !r.embedding
	return r
}

// purposeText is the fixed vocabulary: Chat, Vision, Documents.
func (r rowEntry) purposeText(short bool) string {
	var parts []string
	add := func(long, letter string) {
		if short {
			parts = append(parts, letter)
		} else {
			parts = append(parts, long)
		}
	}
	if r.embedding {
		add("Documents", "D")
	}
	if r.chatable {
		add("Chat", "C")
	}
	if r.vision {
		add("Vision", "V")
	}
	out := ""
	for i, p := range parts {
		if i > 0 && !short {
			out += "+"
		}
		out += p
	}
	return out
}

// needsDownload is a ticked model that isn't on disk yet.
func (r rowEntry) needsDownload() bool { return !r.downloaded }

// left is what is still to download, 0 when unknown.
func (r rowEntry) left() float64 {
	if r.downloaded || r.leftGB == nil {
		return 0
	}
	return *r.leftGB
}

// selection is the user's staged choice: what is ticked and which model chats.
// Nothing is saved until Continue (setup) or Apply (/models).
type selection struct {
	rows    []rowEntry
	ticked  map[string]bool
	chat    string
	initial map[string]bool
	// unticked are models the user unticked by hand, not ones never ticked.
	unticked map[string]bool
	// chatChosen is set once the user picked their own chat model (or saved
	// one earlier), rather than taking GAIA's default for this PC.
	chatChosen bool
	// cloud is a chat that runs on a cloud provider: only the embedder matters.
	cloud bool
}

// newSelection lays the plan out as rows and starts from the backend's own
// selection: recommended models ticked, skipped ones not.
func newSelection(p setupapi.Plan, cloud bool) selection {
	s := selection{ticked: map[string]bool{}, initial: map[string]bool{}, unticked: map[string]bool{},
		chatChosen: p.ChatModel.UserSet, cloud: cloud}
	for _, e := range p.Catalog {
		r := entryFrom(e)
		if cloud && !(r.embedding && r.planned) {
			continue
		}
		s.rows = append(s.rows, r)
		if !r.installed && (r.selected || r.kept()) {
			s.ticked[r.id] = true
			s.initial[r.id] = true
		}
	}
	// Setup's own models first, then GAIA's other chat models, then what is
	// already in Lemonade.
	sort.SliceStable(s.rows, func(i, j int) bool {
		return rank(s.rows[i]) < rank(s.rows[j])
	})
	if !cloud {
		s.chat = p.ChatModel.ID
		if !s.canChat(s.chat) {
			s.chat = s.fallbackChat()
		}
	}
	return s
}

func rank(r rowEntry) int {
	switch {
	case r.planned:
		return 0
	case !r.installed:
		return 1
	}
	return 2
}

func (s selection) find(id string) (rowEntry, bool) {
	for _, r := range s.rows {
		if sameID(r.id, id) {
			return r, true
		}
	}
	return rowEntry{}, false
}

// canChat reports whether id can be the chat model right now: a chat model
// that is ticked, or one already in Lemonade.
func (s selection) canChat(id string) bool {
	r, ok := s.find(id)
	if !ok || !r.chatable {
		return false
	}
	return r.installed || s.ticked[r.id]
}

// chatCandidates are the models the default-chat picker offers.
func (s selection) chatCandidates() []rowEntry {
	var out []rowEntry
	for _, r := range s.rows {
		if s.canChat(r.id) {
			out = append(out, r)
		}
	}
	return out
}

// fallbackChat picks the chat model when the current one is unticked: the
// recommended one if ticked, else the first ticked, else one already in
// Lemonade, else none.
func (s selection) fallbackChat() string {
	var first, installed string
	for _, r := range s.rows {
		if !s.canChat(r.id) {
			continue
		}
		if r.recommended && !r.installed {
			return r.id
		}
		if first == "" && !r.installed {
			first = r.id
		}
		if installed == "" && r.installed {
			installed = r.id
		}
	}
	if first != "" {
		return first
	}
	return installed
}

// kept is a model on this PC that setup no longer downloads: there is no
// skip to save for it, so its tick can't change anything.
func (r rowEntry) kept() bool { return r.downloaded && !r.planned && !r.installed }

// toggle ticks or unticks a row, moving the chat model if it was unticked.
// On a row already in Lemonade it makes that model the chat model instead.
// It reports false when the row's tick can't change (a kept model).
func (s *selection) toggle(id string) bool {
	r, ok := s.find(id)
	if !ok {
		return false
	}
	if r.installed {
		if r.chatable {
			s.chat = r.id
			s.chatChosen = true
		}
		return true
	}
	if r.kept() {
		return false
	}
	s.ticked[r.id] = !s.ticked[r.id]
	if s.ticked[r.id] {
		delete(s.unticked, r.id)
	} else {
		s.unticked[r.id] = true
	}
	if s.cloud {
		return true
	}
	if s.ticked[r.id] && s.chat == "" && r.chatable {
		s.chat = r.id
	}
	if !s.canChat(s.chat) {
		s.chat = s.fallbackChat()
	}
	return true
}

// resetToRecommended ticks the recommended models only.
func (s *selection) resetToRecommended(chat string) {
	s.ticked = map[string]bool{}
	s.unticked = map[string]bool{}
	s.chatChosen = false
	for _, r := range s.rows {
		if !r.installed && r.recommended {
			s.ticked[r.id] = true
		}
	}
	if s.cloud {
		return
	}
	s.chat = chat
	if !s.canChat(s.chat) {
		s.chat = s.fallbackChat()
	}
}

// pending are the ticked models still to download, in the order the app
// becomes usable soonest: the embedder, then the chat model, then the rest.
func (s selection) pending() []rowEntry {
	var out []rowEntry
	for _, r := range s.rows {
		if !r.installed && s.ticked[r.id] && r.needsDownload() {
			out = append(out, r)
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		return s.downloadOrder(out[i]) < s.downloadOrder(out[j])
	})
	return out
}

func (s selection) downloadOrder(r rowEntry) int {
	switch {
	case r.embedding:
		return 0
	case sameID(r.id, s.chat):
		return 1
	}
	return 2
}

// downloadGB is the total still to download.
func (s selection) downloadGB() float64 {
	var total float64
	for _, r := range s.pending() {
		total += r.left()
	}
	return total
}

// shortfall is the disk warning, or "" when the selection fits or free space
// is unknown. The model to drop is the largest pending one whose removal alone
// makes it fit, as the backend's own warning picks it.
func (s selection) shortfall(free *float64, drive string) string {
	if free == nil {
		return ""
	}
	need := s.downloadGB()
	if need <= *free {
		return ""
	}
	short := need - *free
	var largest rowEntry
	for _, r := range s.pending() {
		if r.left() > largest.left() {
			largest = r
		}
	}
	if largest.id != "" && largest.left() >= short {
		return diskShortfall(need, *free, drive, largest.name, largest.left())
	}
	return diskShortfall(need, *free, drive, "", 0)
}

// noFit is the first ticked download that won't fit this PC's memory and has
// not been confirmed yet.
func (s selection) noFit(acked map[string]bool) (rowEntry, bool) {
	for _, r := range s.pending() {
		if r.fits != nil && !*r.fits && !acked[r.id] && r.needGB != nil && r.haveGB != nil {
			return r, true
		}
	}
	return rowEntry{}, false
}

// blocked is the one rule that disables Continue: a local chat with no chat
// model at all.
func (s selection) blocked() bool {
	return !s.cloud && s.chat == ""
}

// wants reports whether the user wants id on this PC after their choices.
func (s selection) wants(id string) bool {
	for k, v := range s.ticked {
		if v && sameID(k, id) {
			return true
		}
	}
	return sameID(id, s.chat) && s.chat != ""
}

// untickedByHand reports whether the user unticked id themselves.
func (s selection) untickedByHand(id string) bool {
	for k, v := range s.unticked {
		if v && sameID(k, id) {
			return true
		}
	}
	return false
}

// dirty reports whether anything differs from what the backend has saved.
func (s selection) dirty(p setupapi.Plan) bool {
	if !s.cloud && !sameID(s.chat, p.ChatModel.ID) {
		return true
	}
	for _, r := range s.rows {
		if r.installed || r.kept() {
			continue
		}
		if s.ticked[r.id] != r.selected {
			return true
		}
	}
	return false
}
