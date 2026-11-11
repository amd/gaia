package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestGuideRecipeDrawsEveryLine(t *testing.T) {
	ingredients := make([]string, 14)
	for i := range ingredients {
		ingredients[i] = "ingredient-" + itoa(i)
	}
	steps := make([]string, 12)
	for i := range steps {
		steps[i] = "Step " + itoa(i+1) + ": do thing-" + itoa(i)
	}
	payload, err := json.Marshal(map[string]any{
		"kind": "guide", "guideType": "recipe", "title": "Banana bread",
		"description": "Moist and easy.", "notes": "Keeps 3 days.",
		"servings": 8, "items": ingredients, "steps": steps,
	})
	if err != nil {
		t.Fatal(err)
	}
	out := Render("guide", payload, width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "Banana bread", "Moist and easy.", "Serves 8",
		"Ingredients", "Steps", "Notes: Keeps 3 days.", "ingredient-13", "12. do thing-11")
	// The model's own "Step N:" prefix goes; the card numbers lines itself.
	assertNotContains(t, out, "Step 1:", "Unsupported card", "raw data")
}

func TestGuideChecklistHasNoStepsSection(t *testing.T) {
	out := Render("guide", raw(t, `{"kind":"guide","guideType":"checklist","title":"Camping",
	  "description":"","notes":"","servings":null,"items":["- Tent","Sleeping bag"],"steps":[]}`), width80)
	assertContains(t, out, "Camping", "Items", "- Tent", "- Sleeping bag")
	assertNotContains(t, out, "Steps", "- - Tent")
}

func TestGuideLabelsFollowTheType(t *testing.T) {
	for typ, want := range map[string][]string{
		"workout":   {"Equipment", "Exercises"},
		"howto":     {"Materials & tools", "Steps"},
		"itinerary": {"Packing", "Days"},
	} {
		out := Render("guide", raw(t, `{"kind":"guide","guideType":"`+typ+`","title":"T",
		  "servings":null,"items":["a"],"steps":["b"]}`), width80)
		assertContains(t, out, want...)
	}
}

func TestGuideBadPayloadSaysSoInsteadOfCrashing(t *testing.T) {
	for name, body := range map[string]string{
		"unknown type":      `{"guideType":"poem","title":"T","items":[],"steps":[]}`,
		"missing title":     `{"guideType":"checklist","title":" ","items":["a"],"steps":[]}`,
		"missing items":     `{"guideType":"checklist","title":"T","steps":[]}`,
		"non-string line":   `{"guideType":"checklist","title":"T","items":[1],"steps":[]}`,
		"recipe no serving": `{"guideType":"recipe","title":"T","items":["a"],"steps":["b"]}`,
		"fractional serves": `{"guideType":"recipe","title":"T","servings":2.5,"items":["a"],"steps":["b"]}`,
		"not an object":     `"recipe"`,
	} {
		out := Render("guide", raw(t, body), width80)
		if !strings.Contains(plain(out), "Invalid guide payload") {
			t.Errorf("%s: want the invalid-payload card, got\n%s", name, plain(out))
		}
	}
}

func TestGuideTextCannotMoveTheCursor(t *testing.T) {
	out := Render("guide", raw(t, `{"guideType":"checklist","title":"T\u001b[2J",
	  "servings":null,"items":["a\u001b[31mred"],"steps":[]}`), width80)
	if strings.Contains(out, "\x1b[2J") || strings.Contains(out, "\x1b[31m") {
		t.Errorf("escape sequence from the payload reached the terminal: %q", out)
	}
}

func TestGuideNarrowTerminal(t *testing.T) {
	out := Render("guide", raw(t, `{"guideType":"howto","title":"Fix a tire",
	  "servings":null,"items":["Tire levers"],"steps":["Remove the wheel by opening the brake and undoing the axle nut"]}`), 20)
	assertWidth(t, out, 20)
}
