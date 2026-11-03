package cards

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strings"
)

// generatedMediaName is one URL-encoded file name, the only thing a generated
// media reference (`/api/media/<kind>/<name>`) may name.
var generatedMediaName = regexp.MustCompile(`^[A-Za-z0-9%._~-]+$`)

// isGeneratedMediaRef reports whether src names a file GAIA generated, served
// by the Agent UI backend from its own output folder (§4.3). A name that
// decodes to a path is rejected.
func isGeneratedMediaRef(kind, src string) bool {
	prefix := "/api/media/" + kind + "/"
	if !strings.HasPrefix(src, prefix) {
		return false
	}
	enc := strings.TrimPrefix(src, prefix)
	if !generatedMediaName.MatchString(enc) {
		return false
	}
	name, err := url.PathUnescape(enc)
	if err != nil || name == "." || name == ".." {
		return false
	}
	return !strings.ContainsAny(name, "/\\:\x00")
}

type audioPayload struct {
	Src             string   `json:"src"`
	Caption         string   `json:"caption"`
	Path            string   `json:"path"`
	DurationSeconds *float64 `json:"duration_seconds"`
}

// renderAudio degrades rather than plays: a terminal has no player, so the card
// says what was made, where it was saved and how long it is.
func renderAudio(data json.RawMessage, width int) string {
	var p audioPayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("audio", err.Error(), data, width)
	}
	if !isGeneratedMediaRef("audio", p.Src) {
		return renderInvalid("audio", "src must be a generated audio file", data, width)
	}
	d := p.DurationSeconds
	if d != nil && (math.IsNaN(*d) || math.IsInf(*d, 0) || *d < 0) {
		return renderInvalid("audio", "duration_seconds must be a non-negative number", data, width)
	}

	label := strings.TrimSpace(p.Caption)
	if label == "" {
		label = "untitled"
	}
	b := newBox("Audio", width)
	b.addWrapped("  ", "[audio: "+label+"]")
	if path := strings.TrimSpace(p.Path); path != "" {
		b.addWrapped("  ", "Saved: "+path)
	}
	if d != nil {
		b.addWrapped("  ", "Length: "+formatDuration(*d))
	}
	b.addWrapped("  ", "Not played — open the file to listen.")
	return b.render()
}

func formatDuration(seconds float64) string {
	total := int(math.Round(seconds))
	return fmt.Sprintf("%d:%02d", total/60, total%60)
}
