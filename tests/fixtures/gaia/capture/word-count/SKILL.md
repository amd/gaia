---
name: word-count
description: Count words in a text precisely using the count_words tool.
version: "1.0.0"
license: MIT
metadata:
  gaia:
    tools:
      - name: count_words
        description: Count the words in a text.
        parameters:
          text: {type: string, required: true}
---

# Word Count

When the user asks how many words a text has, call `word-count/count_words`
with the text and report the exact number it returns. Do not estimate by eye.
