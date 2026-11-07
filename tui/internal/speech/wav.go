// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"encoding/binary"
	"fmt"
	"regexp"
	"strconv"
)

var (
	reL16  = regexp.MustCompile(`(?i)^audio/l16\b`)
	reRate = regexp.MustCompile(`(?i)\brate=(\d+)`)
)

// PCMRate reads the sample rate from Lemonade's "audio/l16;rate=24000"
// Content-Type. Anything else is an error: the bytes would not be 16-bit PCM.
func PCMRate(contentType string) (int, error) {
	m := reRate.FindStringSubmatch(contentType)
	if !reL16.MatchString(contentType) || m == nil {
		if contentType == "" {
			contentType = "no content type"
		}
		return 0, fmt.Errorf("Lemonade returned %q for a pcm speech request; expected "+
			"audio/l16;rate=<hz>. Update Lemonade Server", contentType)
	}
	rate, err := strconv.Atoi(m[1])
	if err != nil || rate <= 0 {
		return 0, fmt.Errorf("Lemonade returned an unusable sample rate in %q", contentType)
	}
	return rate, nil
}

// WAV wraps raw 16-bit little-endian mono PCM in a WAV header.
func WAV(pcm []byte, rate int) ([]byte, error) {
	if len(pcm)%2 != 0 {
		return nil, fmt.Errorf("Lemonade returned %d bytes of 16-bit audio, an odd count, so the audio is truncated", len(pcm))
	}
	const channels, bits = 1, 16
	blockAlign := channels * bits / 8
	out := make([]byte, 44, 44+len(pcm))
	copy(out[0:], "RIFF")
	binary.LittleEndian.PutUint32(out[4:], uint32(36+len(pcm)))
	copy(out[8:], "WAVE")
	copy(out[12:], "fmt ")
	binary.LittleEndian.PutUint32(out[16:], 16) // PCM fmt chunk size
	binary.LittleEndian.PutUint16(out[20:], 1)  // PCM
	binary.LittleEndian.PutUint16(out[22:], channels)
	binary.LittleEndian.PutUint32(out[24:], uint32(rate))
	binary.LittleEndian.PutUint32(out[28:], uint32(rate*blockAlign))
	binary.LittleEndian.PutUint16(out[32:], uint16(blockAlign))
	binary.LittleEndian.PutUint16(out[34:], bits)
	copy(out[36:], "data")
	binary.LittleEndian.PutUint32(out[40:], uint32(len(pcm)))
	return append(out, pcm...), nil
}
