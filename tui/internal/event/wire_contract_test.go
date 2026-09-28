package event

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// canonicalEventsFixture mirrors the "events" half of
// tests/fixtures/stdio/gaia_stdio_wire.json. The Python side asserts the agent
// still emits every field listed there, so these structs and that emitter are
// pinned to the same list.
type canonicalEventsFixture struct {
	Events map[string]struct {
		Fields         []string `json:"fields"`
		StdioExtension []string `json:"stdio_extension"`
	} `json:"events"`
}

func loadCanonicalEventsFixture(t *testing.T) canonicalEventsFixture {
	t.Helper()
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "stdio", "gaia_stdio_wire.json")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in fixture
	if err != nil {
		t.Fatalf("could not read the shared stdio wire fixture at %s: %v", path, err)
	}
	var fixture canonicalEventsFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("could not parse the shared stdio wire fixture: %v", err)
	}
	return fixture
}

// jsonFields lists the wire names a struct decodes, minus the `type` discriminator.
func jsonFields(v interface{}) []string {
	var names []string
	typ := reflect.TypeOf(v)
	for i := 0; i < typ.NumField(); i++ {
		name := strings.Split(typ.Field(i).Tag.Get("json"), ",")[0]
		if name == "" || name == "-" || name == "type" {
			continue
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func TestCanonicalEventStructsMatchTheSharedFixture(t *testing.T) {
	f := loadCanonicalEventsFixture(t)
	structs := map[string]interface{}{
		CanonicalTypeStatus:            CanonicalStatusEvent{},
		CanonicalTypeToken:             CanonicalTokenEvent{},
		CanonicalTypeToolCall:          CanonicalToolCallEvent{},
		CanonicalTypeToolResult:        CanonicalToolResultEvent{},
		CanonicalTypeNeedsConfirmation: CanonicalNeedsConfirmationEvent{},
		CanonicalTypeNeedsInput:        CanonicalNeedsInputEvent{},
		CanonicalTypeFinal:             CanonicalFinalEvent{},
		CanonicalTypeError:             CanonicalErrorEvent{},
	}
	if len(structs) != len(f.Events) {
		t.Errorf("the TUI knows %d canonical types, the fixture lists %d", len(structs), len(f.Events))
	}
	for etype, spec := range f.Events {
		v, ok := structs[etype]
		if !ok {
			t.Errorf("fixture lists %q, which the TUI has no struct for", etype)
			continue
		}
		want := append(append([]string(nil), spec.Fields...), spec.StdioExtension...)
		sort.Strings(want)
		if got := jsonFields(v); strings.Join(got, ",") != strings.Join(want, ",") {
			t.Errorf("%s decodes %v, fixture says %v", etype, got, want)
		}
	}
}
