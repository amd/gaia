package client

import (
	"encoding/json"
	"strings"
	"testing"
)

// The agent reads stdin a LINE at a time. A query written verbatim is therefore
// split at every newline, and each fragment becomes its own turn: pasting five
// commit messages asked five questions, and the agent answered the first one
// insisting it was all it had been sent.
func TestAMultiLineQueryTravelsAsOneLine(t *testing.T) {
	query := "use the changelog skill on these:\nfeat: a\nfix: b\nBREAKING: c"

	line, err := json.Marshal(map[string]string{queryKey: query})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if strings.Contains(string(line), "\n") {
		t.Fatalf("the wire form still contains a newline: %q", line)
	}

	var back map[string]string
	if err := json.Unmarshal(line, &back); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if back[queryKey] != query {
		t.Errorf("query did not survive the trip:\n got %q\nwant %q", back[queryKey], query)
	}
}

// The two stdin envelopes must stay distinguishable, or a question gets
// swallowed as a control message (or worse, the reverse).
func TestQueryAndControlKeysAreDistinct(t *testing.T) {
	if queryKey == controlKey {
		t.Fatal("a query and a control message would be indistinguishable")
	}
}
