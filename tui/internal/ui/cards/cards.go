// Package cards renders a `tool_result`'s typed `render` card into terminal
// text.
//
// The `/query` SSE contract (docs/spec/agent-ui-query-sse-contract.md §4.2/§4.3)
// lets a sidecar declare how its result should be drawn — `email_pre_scan` for
// the email agent's inbox triage, plus five generic primitives any agent can
// emit with no client work. §7 makes the receiving end's obligations explicit:
// an unknown key degrades to a visible generic card with a raw dump, and a
// payload that fails its schema says so. A card is never dropped and never
// blanks the message.
//
// Of the five primitives this package draws three. `image` is base64 raster and
// cannot be shown in a terminal, so it degrades to a caption line; `diff` has no
// producer today and rides the unsupported-card fallback until one exists.
package cards

import (
	"encoding/json"
	"strings"
)

// Render draws the card for a tool_result's `render` key at the given outer
// width. It always returns something visible: unknown keys and malformed
// payloads become explicit fallback cards rather than empty strings.
//
// width is the total width the card may occupy, borders included.
func Render(renderKey string, data json.RawMessage, width int) string {
	key := strings.TrimSpace(renderKey)
	if key == "" {
		return renderUnsupported("(none)", data, width)
	}
	switch key {
	case "email_pre_scan":
		return renderEmailPreScan(data, width)
	case "table":
		return renderTable(data, width)
	case "key_value":
		return renderKeyValue(data, width)
	case "list":
		return renderList(data, width)
	case "image":
		return renderImage(data, width)
	default:
		return renderUnsupported(key, data, width)
	}
}
