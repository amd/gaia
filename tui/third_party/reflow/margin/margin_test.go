package margin

import (
	"errors"
	"testing"

	"github.com/muesli/reflow/indent"

	"github.com/muesli/reflow/padding"
)

func TestMargin(t *testing.T) {
	tt := []struct {
		Input    string
		Expected string
		Width    uint
		Margin   uint
	}{
		// No-op, should pass through:
		{
			"foobar",
			"foobar",
			0,
			0,
		},
		// Basic margin:
		{
			"foobar",
			"  foobar  ",
			10,
			2,
		},
		// Asymmetric margin:
		{
			"foo",
			"  foo ",
			6,
			2,
		},
		// Multi-line margin:
		{
			"foo\nbar",
			" foo \n bar ",
			5,
			1,
		},
		// Don't pad empty trailing lines:
		{
			"foo\nbar\n",
			" foo \n bar \n",
			5,
			1,
		},
		// ANSI sequence codes:
		{
			"\x1B[38;2;249;38;114mfoo",
			"\x1B[38;2;249;38;114m\x1B[0m   \x1B[38;2;249;38;114mfoo   ",
			9,
			3,
		},
	}

	for i, tc := range tt {
		f := NewWriter(tc.Width, tc.Margin, nil)

		_, err := f.Write([]byte(tc.Input))
		if err != nil {
			t.Error(err)
		}
		f.Close()

		if f.String() != tc.Expected {
			t.Errorf("Test %d, expected:\n\n`%s`\n\nActual Output:\n\n`%s`", i, tc.Expected, f.String())
		}
	}
}

func TestMarginString(t *testing.T) {
	actual := String("foobar", 10, 2)
	expected := "  foobar  "
	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func BenchmarkMarginString(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		b.ReportAllocs()
		b.ResetTimer()
		for pb.Next() {
			String("foobar", 10, 2)
		}
	})
}

func TestWriter_Error(t *testing.T) {
	t.Parallel()

	f := &Writer{
		iw: indent.NewWriter(2, nil),
		pw: padding.NewWriterPipe(fakeWriter{}, 10, nil),
	}

	if _, err := f.Write([]byte("foobar")); err != fakeErr {
		t.Error(err)
	}

	f.iw = indent.NewWriterPipe(fakeWriter{}, 2, nil)

	if _, err := f.Write([]byte("foobar")); err != fakeErr {
		t.Error(err)
	}

	if err := f.Close(); err != fakeErr {
		t.Error(err)
	}
}

var fakeErr = errors.New("fake error")

type fakeWriter struct{}

func (fakeWriter) Write(_ []byte) (int, error) {
	return 0, fakeErr
}
