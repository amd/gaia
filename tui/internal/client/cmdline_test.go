package client

import (
	"reflect"
	"testing"
)

func TestSplitCommandLine(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want []string
	}{
		{"plain", `gaia-bash --json-events`, []string{"gaia-bash", "--json-events"}},
		{"extra whitespace", "  gaia-bash \t --json-events  ", []string{"gaia-bash", "--json-events"}},
		{
			"double-quoted path with space",
			`"/Users/me/My Agents/gaia-bash" --json-events`,
			[]string{"/Users/me/My Agents/gaia-bash", "--json-events"},
		},
		{
			"single-quoted path with space",
			`'/Users/me/My Agents/gaia-bash' --model Gemma-4-E4B-it-GGUF`,
			[]string{"/Users/me/My Agents/gaia-bash", "--model", "Gemma-4-E4B-it-GGUF"},
		},
		{
			"escaped space",
			`/Users/me/My\ Agents/gaia-bash`,
			[]string{"/Users/me/My Agents/gaia-bash"},
		},
		{"empty quoted arg", `agent --flag ""`, []string{"agent", "--flag", ""}},
		{"quote inside word", `agent --msg="hello world"`, []string{"agent", "--msg=hello world"}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := SplitCommandLine(tc.in)
			if err != nil {
				t.Fatalf("SplitCommandLine(%q): %v", tc.in, err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("SplitCommandLine(%q) = %#v, want %#v", tc.in, got, tc.want)
			}
		})
	}
}

func TestSplitCommandLineErrors(t *testing.T) {
	cases := []struct {
		name string
		in   string
	}{
		{"empty", ""},
		{"whitespace only", "   \t "},
		{"unbalanced double quote", `agent "unterminated`},
		{"unbalanced single quote", `agent 'unterminated`},
		{"dangling backslash", `agent \`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got, err := SplitCommandLine(tc.in); err == nil {
				t.Fatalf("SplitCommandLine(%q) = %#v, want an error", tc.in, got)
			}
		})
	}
}
