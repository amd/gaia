package wrap

import (
	"testing"
)

func TestWrap(t *testing.T) {
	tt := []struct {
		Input         string
		Expected      string
		Limit         int
		KeepNewlines  bool
		PreserveSpace bool
		TabWidth      int
	}{
		// No-op, should pass through, including trailing whitespace:
		{
			Input:         "foobar\n ",
			Expected:      "foobar\n ",
			Limit:         0,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// Nothing to wrap here, should pass through:
		{
			Input:         "foo",
			Expected:      "foo",
			Limit:         4,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// In contrast to wordwrap we break a long word to obey the given limit
		{
			Input:         "foobarfoo",
			Expected:      "foob\narfo\no",
			Limit:         4,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// Newlines in the input are respected if desired
		{
			Input:         "f\no\nobar",
			Expected:      "f\no\noba\nr",
			Limit:         3,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// Newlines in the input can be ignored if desired
		{
			Input:         "f\no\nobar",
			Expected:      "foo\nbar",
			Limit:         3,
			KeepNewlines:  false,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// Leading whitespaces after forceful line break can be preserved if desired
		{
			Input:         "foo bar\n  baz",
			Expected:      "foo\n ba\nr\n  b\naz",
			Limit:         3,
			KeepNewlines:  true,
			PreserveSpace: true,
			TabWidth:      0,
		},
		// Leading whitespaces after forceful line break can be removed if desired
		{
			Input:         "foo bar\n  baz",
			Expected:      "foo\nbar\n  b\naz",
			Limit:         3,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// Tabs are broken up according to the configured TabWidth
		{
			Input:         "foo\tbar",
			Expected:      "foo \n  ba\nr",
			Limit:         4,
			KeepNewlines:  true,
			PreserveSpace: true,
			TabWidth:      3,
		},
		// Remaining width of wrapped tab is ignored when space is not preserved
		{
			Input:         "foo\tbar",
			Expected:      "foo \nbar",
			Limit:         4,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      3,
		},
		// ANSI sequence codes don't affect length calculation:
		{
			Input:         "\x1B[38;2;249;38;114mfoo\x1B[0m\x1B[38;2;248;248;242m \x1B[0m\x1B[38;2;230;219;116mbar\x1B[0m",
			Expected:      "\x1B[38;2;249;38;114mfoo\x1B[0m\x1B[38;2;248;248;242m \x1B[0m\x1B[38;2;230;219;116mbar\x1B[0m",
			Limit:         7,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
		// ANSI control codes don't get wrapped:
		{
			Input:         "\x1B[38;2;249;38;114m(\x1B[0m\x1B[38;2;248;248;242mjust another test\x1B[38;2;249;38;114m)\x1B[0m",
			Expected:      "\x1B[38;2;249;38;114m(\x1B[0m\x1B[38;2;248;248;242mju\nst \nano\nthe\nr t\nest\x1B[38;2;249;38;114m\n)\x1B[0m",
			Limit:         3,
			KeepNewlines:  true,
			PreserveSpace: false,
			TabWidth:      0,
		},
	}

	for i, tc := range tt {
		f := NewWriter(tc.Limit)
		f.KeepNewlines = tc.KeepNewlines
		f.PreserveSpace = tc.PreserveSpace
		f.TabWidth = tc.TabWidth

		_, err := f.Write([]byte(tc.Input))
		if err != nil {
			t.Error(err)
		}

		if f.String() != tc.Expected {
			t.Errorf("Test %d, expected:\n\n`%s`\n\nActual Output:\n\n`%s`", i, tc.Expected, f.String())
		}
	}
}

func TestWrapString(t *testing.T) {
	actual := String("foo bar", 3)
	expected := "foo\nbar"
	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}
