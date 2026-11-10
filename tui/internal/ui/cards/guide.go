package cards

import (
	"encoding/json"
	"regexp"
	"strings"
)

// guide is contract §4.3's recipe/workout/how-to/checklist/itinerary card. The
// agent tells the model not to repeat the card's lines, so the card is the
// whole answer: every line is drawn, with no maxCardRows cap.
type guidePayload struct {
	GuideType   string    `json:"guideType"`
	Title       string    `json:"title"`
	Description *string   `json:"description"`
	Notes       *string   `json:"notes"`
	Servings    *float64  `json:"servings"`
	Items       *[]string `json:"items"`
	Steps       *[]string `json:"steps"`
}

type guideLabels struct{ items, steps string }

var guideKinds = map[string]guideLabels{
	"recipe":    {"Ingredients", "Steps"},
	"workout":   {"Equipment", "Exercises"},
	"howto":     {"Materials & tools", "Steps"},
	"checklist": {"Items", ""},
	"itinerary": {"Packing", "Days"},
}

// guideListMarker strips a model's own "Step 2:", "3." or "- ", since the card
// numbers and bullets lines itself (same rule as the Agent UI).
var guideListMarker = regexp.MustCompile(`(?i)^(?:step\s+\d+\s*[:.)-]|\d+[.)]|[-*•])\s+`)

func guideLines(raw []string) []string {
	var out []string
	for _, line := range raw {
		line = guideListMarker.ReplaceAllString(strings.TrimSpace(line), "")
		if line = strings.TrimSpace(line); line != "" {
			out = append(out, line)
		}
	}
	if len(out) > renderCap {
		out = out[:renderCap]
	}
	return out
}

func renderGuide(data json.RawMessage, width int) string {
	var p guidePayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("guide", err.Error(), data, width)
	}
	labels, ok := guideKinds[p.GuideType]
	switch {
	case !ok:
		return renderInvalid("guide", "unknown guideType \""+clean(p.GuideType)+"\"", data, width)
	case strings.TrimSpace(p.Title) == "":
		return renderInvalid("guide", "title is required", data, width)
	case p.Items == nil || p.Steps == nil:
		return renderInvalid("guide", "items and steps are required", data, width)
	case p.GuideType == "recipe" && (p.Servings == nil || *p.Servings < 1 || *p.Servings != float64(int(*p.Servings))):
		return renderInvalid("guide", "a recipe needs servings as a whole number of at least 1", data, width)
	}

	b := newBox(strings.TrimSpace(p.Title), width)
	if p.Description != nil && strings.TrimSpace(*p.Description) != "" {
		b.addWrapped("  ", strings.TrimSpace(*p.Description))
	}
	if p.GuideType == "recipe" {
		b.add("  Serves " + itoa(int(*p.Servings)))
	}

	items := guideLines(*p.Items)
	if len(items) > 0 {
		if len(b.lines) > 0 {
			b.blank()
		}
		b.add("  " + labels.items)
		for _, it := range items {
			addMarked(b, "-", 1, it)
		}
	}

	steps := guideLines(*p.Steps)
	if labels.steps != "" && len(steps) > 0 {
		if len(b.lines) > 0 {
			b.blank()
		}
		b.add("  " + labels.steps)
		markerW := visualLen(itoa(len(steps))) + 1
		for i, step := range steps {
			addMarked(b, itoa(i+1)+".", markerW, step)
		}
	}

	if p.Notes != nil && strings.TrimSpace(*p.Notes) != "" {
		b.blank()
		b.addWrapped("  ", "Notes: "+strings.TrimSpace(*p.Notes))
	}
	if len(b.lines) == 0 {
		b.add("  (no items)")
	}
	return b.render()
}

// addMarked draws one bulleted or numbered line, hanging its wrap under the text.
func addMarked(b *box, marker string, markerW int, text string) {
	lines := wrap(text, b.inner()-2-markerW-1)
	b.add("  " + padTo(marker, markerW) + " " + lines[0])
	for _, cont := range lines[1:] {
		b.add("  " + strings.Repeat(" ", markerW) + " " + cont)
	}
}
