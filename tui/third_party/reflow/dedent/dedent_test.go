package dedent

import (
	"testing"
)

func TestDedent(t *testing.T) {
	tt := []struct {
		Input    string
		Expected string
	}{
		{
			Input:    "      --help      Show help for command\n      --version   Show version\n",
			Expected: "--help      Show help for command\n--version   Show version\n",
		},
		{
			Input:    "      --help              Show help for command\n  -C, --config string   Specify the config file to use\n",
			Expected: "    --help              Show help for command\n-C, --config string   Specify the config file to use\n",
		},
		{
			Input:    "  line 1\n\n  line 2\n line 3",
			Expected: " line 1\n\n line 2\nline 3",
		},
		{
			Input:    "  line 1\n  line 2\n  line 3\n\n",
			Expected: "line 1\nline 2\nline 3\n\n",
		},
		{
			Input:    " \tline 1\n\t\tline 2\n\t line 3\n\n",
			Expected: "line 1\nline 2\nline 3\n\n",
		},
		{
			Input:    "\t\tline 1\n\n\t\tline 2\n\tline 3",
			Expected: "\tline 1\n\n\tline 2\nline 3",
		},
		{
			Input:    "\n\n\n\n\n\n",
			Expected: "\n\n\n\n\n\n",
		},
		{
			Input:    "",
			Expected: "",
		},
	}

	for i, tc := range tt {
		s := String(tc.Input)
		if s != tc.Expected {
			t.Errorf("Test %d, expected:\n\n`%s`\n\nActual Output:\n\n`%s`", i, tc.Expected, s)
		}
	}
}

// go test -bench=BenchmarkDedent -benchmem -count=4
func BenchmarkDedent(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		input := "  line 1\n\n  line 2\n line 3"
		b.ReportAllocs()
		b.ResetTimer()
		for pb.Next() {
			String(input)
		}
	})
}
