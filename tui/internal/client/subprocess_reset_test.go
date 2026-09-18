package client

import (
	"bytes"
	"encoding/json"
	"io"
	"testing"
)

type resetTestWriter struct{ io.Writer }

func (resetTestWriter) Close() error { return nil }

// Compile-time: the subprocess transport must clear the child's history on
// /clear, not just the view (TranscriptResetter).
var _ TranscriptResetter = (*SubprocessClient)(nil)

func TestResetTranscriptSendsClearHistoryControl(t *testing.T) {
	var buf bytes.Buffer
	s := &SubprocessClient{}
	s.stdin = resetTestWriter{&buf}
	s.started = true

	s.ResetTranscript()

	var msg map[string]interface{}
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &msg); err != nil {
		t.Fatalf("control line is not JSON: %v (line %q)", err, buf.String())
	}
	if msg["gaia_control"] != "clear_history" {
		t.Fatalf("wrong control verb: %v", msg["gaia_control"])
	}
}
