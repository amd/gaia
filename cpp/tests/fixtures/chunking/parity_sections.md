# GAIA Retrieval Notes

The retrieval stack indexes documents locally so a question can be answered
without sending the source text to a hosted model. Everything below is a
description of that pipeline as it exists in the Python runtime today.

## Extraction

Text files are read as UTF-8, with a latin-1 fallback for legacy encodings.
Markdown is treated as text; the headers are what the splitter later uses to
find section boundaries. Source files go down the same path.

## Chunking

Chunks are sized in estimated tokens, where one token is approximated as four
characters. A paragraph that fits inside the budget is kept whole so a single
idea does not straddle two chunks. A paragraph that does not fit is split on
sentence boundaries instead of on words.

---

## Overlap

Each chunk carries the tail of its predecessor. Without that overlap a question
whose answer spans a boundary retrieves half an answer, which reads as a
confident but incomplete response. The overlap is trimmed to a word boundary.

## Encoding Notes

A café menu, a naïve reader, and a résumé with accents all round-trip through
the extractor unchanged. Counting is done in code points, not bytes, so
multi-byte text does not shift chunk boundaries between the two runtimes.

## Limits

The index is bounded by a maximum file count and a maximum chunk count. When
either limit is reached the SDK refuses to index more rather than silently
evicting content a user still expects to be searchable.
