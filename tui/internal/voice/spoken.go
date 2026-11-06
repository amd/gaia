// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package voice is the TUI's half of dictation and voice mode: the socket to
// the daemon's voice host, the <spoken> split of a streamed answer, and the
// messages for each failure code. See docs/plans/voice.mdx.
package voice

import (
	"strings"
	"unicode"
)

const (
	openTag  = "<spoken>"
	closeTag = "</spoken>"
)

// MissingSpokenLine is spoken when the answer has no <spoken> block, so a
// format slip is audible as a known line instead of the detail read aloud.
const MissingSpokenLine = "Done. The details are on screen."

// Channel says where a piece of a streamed answer goes.
type Channel int

const (
	// Spoken is the short summary inside <spoken>…</spoken>.
	Spoken Channel = iota
	// Detail is everything after it, shown on screen.
	Detail
)

// Piece is one routed run of answer text, tags removed.
type Piece struct {
	Channel Channel
	Text    string
}

type splitState int

const (
	stateStart splitState = iota
	stateSpoken
	stateGap
	stateDetail
)

// SpokenSplitter splits <spoken>…</spoken> off an answer as it streams. It is
// gaia.voice.spoken.SpokenSplitter in Go, and both pass
// tests/fixtures/voice/spoken_cases.json. Leading whitespace before the open
// tag is ignored; any other text first means the block is missing.
type SpokenSplitter struct {
	state   splitState
	pending string
	spoken  []string
	missing bool
}

// Feed takes the next delta and returns the pieces it settles.
func (s *SpokenSplitter) Feed(text string) []Piece {
	var out []Piece
	s.pending += text
	for s.pending != "" {
		switch s.state {
		case stateStart:
			stripped := strings.TrimLeftFunc(s.pending, unicode.IsSpace)
			if stripped == "" {
				return out
			}
			if strings.HasPrefix(stripped, openTag) {
				s.pending = stripped[len(openTag):]
				s.state = stateSpoken
				continue
			}
			if strings.HasPrefix(openTag, stripped) {
				return out // might still become the tag
			}
			s.missing = true
			s.state = stateDetail
			s.pending = stripped
			continue
		case stateSpoken:
			if idx := strings.Index(s.pending, closeTag); idx >= 0 {
				piece := s.pending[:idx]
				s.pending = s.pending[idx+len(closeTag):]
				s.state = stateGap
				if piece != "" {
					s.spoken = append(s.spoken, piece)
					out = append(out, Piece{Spoken, piece})
				}
				continue
			}
			keep := partialSuffix(s.pending, closeTag)
			piece := s.pending[:len(s.pending)-keep]
			s.pending = s.pending[len(piece):]
			if piece != "" {
				s.spoken = append(s.spoken, piece)
				out = append(out, Piece{Spoken, piece})
			}
			return out
		case stateGap:
			// Whitespace between the block and the detail is dropped,
			// however the stream happens to split it.
			s.pending = strings.TrimLeftFunc(s.pending, unicode.IsSpace)
			if s.pending == "" {
				return out
			}
			s.state = stateDetail
		}
		out = append(out, Piece{Detail, s.pending})
		s.pending = ""
	}
	return out
}

// Reset starts over: a tool step began, so what came before was narration.
func (s *SpokenSplitter) Reset() {
	*s = SpokenSplitter{}
}

// Finish flushes what is left when the answer ends.
func (s *SpokenSplitter) Finish() []Piece {
	var out []Piece
	switch {
	case s.state == stateStart:
		if t := strings.TrimSpace(s.pending); t != "" {
			out = append(out, Piece{Detail, t})
		}
		s.missing = true
	case s.state == stateSpoken:
		// An unclosed block: what was said stands, the slip is recorded.
		if s.pending != "" {
			s.spoken = append(s.spoken, s.pending)
			out = append(out, Piece{Spoken, s.pending})
		}
		s.missing = s.SpokenText() == ""
	case strings.TrimSpace(s.pending) != "" || (s.state == stateDetail && s.pending != ""):
		out = append(out, Piece{Detail, s.pending})
	}
	s.pending = ""
	s.state = stateDetail
	return out
}

// SpokenText is the summary so far, whitespace collapsed.
func (s *SpokenSplitter) SpokenText() string {
	return strings.Join(strings.Fields(strings.Join(s.spoken, "")), " ")
}

// Missing reports an answer that had no <spoken> block.
func (s *SpokenSplitter) Missing() bool { return s.missing }

// partialSuffix is the length of the longest suffix of text that is a prefix
// of tag.
func partialSuffix(text, tag string) int {
	n := len(tag) - 1
	if len(text) < n {
		n = len(text)
	}
	for ; n > 0; n-- {
		if strings.HasSuffix(text, tag[:n]) {
			return n
		}
	}
	return 0
}

// SplitSpoken splits a whole answer: the spoken lead (whitespace collapsed),
// the detail (trimmed), and whether the block was missing.
func SplitSpoken(answer string) (spoken, detail string, missing bool) {
	var s SpokenSplitter
	pieces := append(s.Feed(answer), s.Finish()...)
	var b strings.Builder
	for _, p := range pieces {
		if p.Channel == Detail {
			b.WriteString(p.Text)
		}
	}
	return s.SpokenText(), strings.TrimSpace(b.String()), s.Missing()
}

// DisplayParts is what the chat shows for a stored answer: the spoken lead
// and the detail. An answer with no <spoken> block comes back unchanged with
// an empty lead, so ordinary text answers are never touched.
func DisplayParts(answer string) (lead, detail string) {
	if !strings.HasPrefix(strings.TrimLeftFunc(answer, unicode.IsSpace), openTag) {
		return "", answer
	}
	spoken, rest, _ := SplitSpoken(answer)
	return spoken, rest
}
