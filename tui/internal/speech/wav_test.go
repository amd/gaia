// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestWAVHeaderDescribesMono16BitPCM(t *testing.T) {
	pcm := []byte{1, 0, 2, 0, 3, 0, 4, 0}
	wav, err := WAV(pcm, 24000)
	if err != nil {
		t.Fatal(err)
	}
	le := binary.LittleEndian
	checks := []struct {
		name      string
		got, want any
	}{
		{"RIFF", string(wav[0:4]), "RIFF"},
		{"riff size", le.Uint32(wav[4:]), uint32(36 + len(pcm))},
		{"WAVE", string(wav[8:12]), "WAVE"},
		{"fmt", string(wav[12:16]), "fmt "},
		{"fmt size", le.Uint32(wav[16:]), uint32(16)},
		{"format", le.Uint16(wav[20:]), uint16(1)},
		{"channels", le.Uint16(wav[22:]), uint16(1)},
		{"rate", le.Uint32(wav[24:]), uint32(24000)},
		{"byte rate", le.Uint32(wav[28:]), uint32(48000)},
		{"block align", le.Uint16(wav[32:]), uint16(2)},
		{"bits", le.Uint16(wav[34:]), uint16(16)},
		{"data", string(wav[36:40]), "data"},
		{"data size", le.Uint32(wav[40:]), uint32(len(pcm))},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s = %v, want %v", c.name, c.got, c.want)
		}
	}
	if !bytes.Equal(wav[44:], pcm) {
		t.Error("samples after the header changed")
	}
}

func TestWAVRefusesAnOddByteCount(t *testing.T) {
	if _, err := WAV([]byte{1, 2, 3}, 24000); err == nil {
		t.Fatal("an odd byte count is truncated 16-bit audio and must be refused")
	}
}

func TestPCMRateReadsLemonadesContentType(t *testing.T) {
	rate, err := PCMRate("audio/l16;rate=24000;channels=1")
	if err != nil || rate != 24000 {
		t.Fatalf("got %d, %v", rate, err)
	}
	for _, ct := range []string{"", "audio/mpeg", "audio/l16", "audio/wav;rate=24000"} {
		if _, err := PCMRate(ct); err == nil {
			t.Errorf("%q was accepted as 16-bit PCM", ct)
		}
	}
}
