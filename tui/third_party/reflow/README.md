# reflow

[![Latest Release](https://img.shields.io/github/release/muesli/reflow.svg)](https://github.com/muesli/reflow/releases)
[![Build Status](https://github.com/muesli/reflow/workflows/build/badge.svg)](https://github.com/muesli/reflow/actions)
[![Coverage Status](https://coveralls.io/repos/github/muesli/reflow/badge.svg?branch=master)](https://coveralls.io/github/muesli/reflow?branch=master)
[![Go ReportCard](http://goreportcard.com/badge/muesli/reflow)](http://goreportcard.com/report/muesli/reflow)
[![GoDoc](https://godoc.org/github.com/golang/gddo?status.svg)](https://pkg.go.dev/github.com/muesli/reflow)

A collection of ANSI-aware methods and `io.Writers` helping you to transform
blocks of text. This means you can still style your terminal output with ANSI
escape sequences without them affecting the reflow operations & algorithms.

## Word-Wrapping

The `wordwrap` package lets you word-wrap strings or entire blocks of text.

```go
import "github.com/muesli/reflow/wordwrap"

s := wordwrap.String("Hello World!", 5)
fmt.Println(s)
```

Result:
```
Hello
World!
```

The word-wrapping Writer is compatible with the `io.Writer` / `io.WriteCloser` interfaces:

```go
f := wordwrap.NewWriter(limit)
f.Write(b)
f.Close()

fmt.Println(f.String())
```

Customize word-wrapping behavior:

```go
f := wordwrap.NewWriter(limit)
f.Breakpoints = []rune{':', ','}
f.Newline = []rune{'\r'}
```

## Unconditional Wrapping

The `wrap` package lets you unconditionally wrap strings or entire blocks of text.

```go
import "github.com/muesli/reflow/wrap"

s := wrap.String("Hello World!", 7)
fmt.Println(s)
```

Result:
```
Hello W
orld!
```

The unconditional wrapping Writer is compatible with the `io.Writer` interfaces:

```go
f := wrap.NewWriter(limit)
f.Write(b)

fmt.Println(f.String())
```

Customize word-wrapping behavior:

```go
f := wrap.NewWriter(limit)
f.Newline = []rune{'\r'}
f.KeepNewlines = false
f.reserveSpace = true
f.TabWidth = 2
```

**Tip:** This wrapping method can be used in conjunction with word-wrapping when word-wrapping is preferred but a line limit has to be enforced:

```go
wrapped := wrap.String(wordwrap.String("Just an example", 5), 5)
fmt.Println(wrapped)
```

Result:
```
Just
an
examp
le
```


### ANSI Example

```go
s := wordwrap.String("I really \x1B[38;2;249;38;114mlove\x1B[0m Go!", 8)
fmt.Println(s)
```

Result:

![ANSI Example Output](https://github.com/muesli/reflow/blob/master/reflow.png)

## Indentation

The `indent` package lets you indent strings or entire blocks of text.

```go
import "github.com/muesli/reflow/indent"

s := indent.String("Hello World!", 4)
fmt.Println(s)
```

Result: `    Hello World!`

There is also an indenting Writer, which is compatible with the `io.Writer`
interface:

```go
// indent uses spaces per default:
f := indent.NewWriter(width, nil)

// but you can also use a custom indentation function:
f = indent.NewWriter(width, func(w io.Writer) {
    w.Write([]byte("."))
})

f.Write(b)
f.Close()

fmt.Println(f.String())
```

## Dedentation

The `dedent` package lets you dedent strings or entire blocks of text.

```go
import "github.com/muesli/reflow/dedent"

input := `    Hello World!
  Hello World!
`

s := dedent.String(input)
fmt.Println(s)
```

Result:

```
  Hello World!
Hello World!
```

## Padding

The `padding` package lets you pad strings or entire blocks of text.

```go
import "github.com/muesli/reflow/padding"

s := padding.String("Hello", 8)
fmt.Println(s)
```

Result: `Hello___` (the underlined portion represents 3 spaces)

There is also a padding Writer, which is compatible with the `io.WriteCloser`
interface:

```go
// padding uses spaces per default:
f := padding.NewWriter(width, nil)

// but you can also use a custom padding function:
f = padding.NewWriter(width, func(w io.Writer) {
    w.Write([]byte("."))
})

f.Write(b)
f.Close()

fmt.Println(f.String())
```
