// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"context"
	"fmt"
	"os"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// Synthesizer turns one piece of text into a WAV file's bytes.
type Synthesizer func(ctx context.Context, text string) ([]byte, error)

// PlayFunc plays the WAV file at path and returns when it has finished.
type PlayFunc func(ctx context.Context, path string) error

// Settings is how a reply is read: which voice, how fast.
type Settings struct {
	Voice string
	Speed int // percent of normal, MinSpeed..MaxSpeed
}

// SpeechRequest is the /audio/speech body for text under s.
func (s Settings) SpeechRequest(text string) lemonade.SpeechRequest {
	return lemonade.SpeechRequest{
		Model:          lemonade.SpeechModel,
		Input:          text,
		Voice:          s.Voice,
		Speed:          float64(s.Speed) / 100,
		ResponseFormat: "pcm",
	}
}

// LemonadeSynthesizer speaks through c in s's voice and speed.
func LemonadeSynthesizer(c *lemonade.Client, s Settings) Synthesizer {
	return func(ctx context.Context, text string) ([]byte, error) {
		pcm, contentType, err := c.Synthesize(ctx, s.SpeechRequest(text))
		if err != nil {
			return nil, err
		}
		rate, err := PCMRate(contentType)
		if err != nil {
			return nil, err
		}
		return WAV(pcm, rate)
	}
}

type fetched struct {
	path string
	err  error
}

// Speak plays pieces back to back, fetching the next while the current one
// plays. It returns when the last piece has played, on the first failure, or
// when ctx is cancelled. Every temporary file it wrote is gone when the last
// fetch it started has finished.
func Speak(ctx context.Context, pieces []string, synth Synthesizer, play PlayFunc) error {
	if len(pieces) == 0 {
		return nil
	}
	// Read once: a fetch can still be writing after Speak has returned.
	dir := tempDir
	fetch := func(text string) <-chan fetched {
		ch := make(chan fetched, 1)
		go func() {
			wav, err := synth(ctx, text)
			if err != nil {
				ch <- fetched{err: err}
				return
			}
			path, err := writeTemp(dir, wav)
			ch <- fetched{path: path, err: err}
		}()
		return ch
	}
	next := fetch(pieces[0])
	defer func() {
		if next == nil {
			return
		}
		// A fetch still running when we stop: remove its file once it lands.
		pending := next
		go func() {
			if r := <-pending; r.path != "" {
				os.Remove(r.path)
			}
		}()
	}()
	for i := range pieces {
		var r fetched
		select {
		case r = <-next:
			next = nil
		case <-ctx.Done():
			return ctx.Err()
		}
		if r.err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return r.err
		}
		if i+1 < len(pieces) {
			next = fetch(pieces[i+1])
		}
		err := play(ctx, r.path)
		os.Remove(r.path)
		if err != nil {
			return err
		}
	}
	return nil
}

// tempDir is where audio files are written; "" is the system's temp dir.
var tempDir = ""

func writeTemp(dir string, wav []byte) (string, error) {
	f, err := os.CreateTemp(dir, "gaia-read-aloud-*.wav")
	if err != nil {
		return "", fmt.Errorf("could not create a temporary audio file: %w", err)
	}
	_, werr := f.Write(wav)
	cerr := f.Close()
	if werr != nil || cerr != nil {
		os.Remove(f.Name())
		if werr == nil {
			werr = cerr
		}
		return "", fmt.Errorf("could not write the temporary audio file %s: %w", f.Name(), werr)
	}
	return f.Name(), nil
}
