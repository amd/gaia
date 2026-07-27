package cards

import (
	"bytes"
	"encoding/json"
	"strings"
)

// rawDumpLines caps the collapsed raw-data dump. The contract wants the payload
// reachable so a broken producer is debuggable; it does not want a 500-row
// scroll trap in the transcript.
const rawDumpLines = 12

// renderUnsupported is contract §7's unknown-`render` degradation: name the key,
// say why nothing better was drawn, and show the payload.
func renderUnsupported(key string, data json.RawMessage, width int) string {
	b := newBox("Unsupported card", width)
	b.addWrapped("  ", "Unsupported card type: \""+key+"\"")
	b.addWrapped("  ", "This build of GAIA has no renderer for it. The agent's raw result is below.")
	appendRawDump(b, data)
	return b.render()
}

// renderInvalid is contract §7's schema-invalid degradation. reason names the
// specific decode failure so the producer can be fixed, not just noticed.
func renderInvalid(key, reason string, data json.RawMessage, width int) string {
	b := newBox("Invalid card", width)
	b.addWrapped("  ", "Invalid "+key+" payload: "+reason)
	appendRawDump(b, data)
	return b.render()
}

func appendRawDump(b *box, data json.RawMessage) {
	b.blank()
	if len(bytes.TrimSpace(data)) == 0 {
		b.add("  raw data: (empty)")
		return
	}
	b.add("  raw data:")
	lines := prettyJSONLines(data)
	shown := lines
	if len(shown) > rawDumpLines {
		shown = shown[:rawDumpLines]
	}
	for _, line := range shown {
		b.add("  " + truncTo(line, b.inner()-2))
	}
	if n := len(lines) - len(shown); n > 0 {
		b.add("  +" + itoa(n) + " more lines (truncated)")
	}
}

func prettyJSONLines(data json.RawMessage) []string {
	var buf bytes.Buffer
	if err := json.Indent(&buf, data, "", "  "); err != nil {
		// Not valid JSON at all. Show the bytes rather than nothing, but scrub
		// them first — unlike the json.Indent path (which leaves an escape as the
		// literal text ``), these are raw bytes headed for the terminal.
		lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
		for i := range lines {
			lines[i] = clean(lines[i])
		}
		return lines
	}
	return strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
}
