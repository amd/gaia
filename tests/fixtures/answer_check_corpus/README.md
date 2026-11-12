# Answer-check corpus

Each `.json` file here is one real turn: what the user asked, what the model
said, what its tools returned. `tests/unit/agents/test_answer_check_corpus.py`
replays every file through GAIA's own answer checks with no model and asserts
what GAIA added: a note, a message back to the model, or nothing.

The checks are the notes and nudges GAIA adds by itself ("I couldn't confirm all
of this", "I haven't confirmed this works", "Look with your tools first", "I
didn't finish"). A check that fires on a correct answer tells the user verified
work is unverified; one that stays silent lets a made-up save through. Both
kinds of mistake belong here, as a pair: one case where the check **must not
fire**, one where it **must**.

## Add a case from a real transcript

1. Copy the closest existing file and rename it. The name is the test id, so
   say what must happen: `f10-answer-quotes-the-edits-own-backup.json`.
2. Fill it in from the transcript (the session's trace, or the sidecar log):
   - `request`: the user's message, word for word.
   - `model`: the model's replies in order. A tool call is
     `{"tool": ..., "args": ..., "result": ...}` with the result the real tool
     returned; an answer is `{"answer": ...}`.
   - `creates`, on a tool call: files that call wrote, as `{path: text}`. The
     checks look at the disk, so a write that happened must happen here too.
   - `files`: what was in the folder before the turn.
   - `if_sent_back`: what the model said when GAIA sent its answer back. Leave
     it out and the model repeats its last answer.
3. Replace the folder the turn ran in with `{ws}` everywhere, and remove
   anything private. The replay runs in an empty temporary folder.
4. Say what must be true in `expect`:
   - `model_calls`: how many times the model was called. One more than the
     replies you recorded means GAIA sent the answer back.
   - `answer_has` / `answer_lacks`: text in the final answer.
   - `model_told` / `model_never_told`: text GAIA sent the model as a message.
5. Run it:
   `python -m pytest tests/unit/agents/test_answer_check_corpus.py -k <file name>`

`finding` names the issue the turn came from and `why` says, in one sentence,
what a reader should take from it. Both are required.

## A case that fails today

A new case that fails is a bug, and that is the point of adding it. Search for
an existing issue, file one if there is none, and add
`"xfail": "amd/gaia#<issue>, ..."` to the case. The marker is strict: the day
the bug is fixed the test fails until that line is deleted, so the fix and the
corpus cannot drift apart. Never loosen `expect` to make a case pass.

## Other kinds

`"kind": "turn"` is the replay described above. Two smaller kinds cover what a
turn replay cannot reach:

- `"kind": "stream"`: `chunks` the provider streamed; `expect.shown_has` /
  `shown_lacks` is what the chat may show as answer text.
- `"kind": "question"`: a `question` the agent asked through
  `request_user_input` and the `reply` (`null` when nobody answered);
  `expect.result_has` / `result_is_not` is what the model is told.
