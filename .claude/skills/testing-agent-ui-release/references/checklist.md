# Always-check areas

Every release pass covers these, whatever the diff says. Each line is a defect class
an earlier pass found. Add a `test_report.py add` item per area, and add a line here
whenever a person finds a bug the pass missed.

## First run and setup (fresh `--home`)

- Setup reaches a first chat with no hidden question. Progress has stalled at 70%
  while a prompt waited behind another window.
- No false "GAIA agent isn't installed" banner right after "GAIA is ready".
- Install text is plain language: no internal step names ("GAIA-INIT"), and no doubled
  backslashes in log paths.
- With no model server (`--lemonade` pointed at a dead port), the app says what is
  wrong and offers a way forward inside the app. It never tells the person to run a
  command.
- Upgrade over the previous release: the agent binary really updates. A locked old
  binary once made upgrades silently keep the old agent.

## Window, tray, quit and restart

- Notices at the top of the window keep their button clear of the Windows title-bar
  buttons (#6534), and the button strip dims with a modal (#6542).
- Closing the window hides GAIA to the tray, with the first-close explainer once.
- Quit from the tray actually exits, the first time. A second quit in the same tick
  used to be dropped.
- The crash dialog's buttons work.
- The tray and taskbar icons are correct.
- After a relaunch, the app reopens on the last model and the last chat.

## Model picker

- Choosing a cloud model shows its consent where you clicked, not below the last row (#6535).
- Picking an undownloaded model downloads it **and switches to it**.
- The dropdown text is short. Long names truncate at their end, in Arabic too.
- Only models this PC can run are offered. The NPU entry appears only on a PC with an
  NPU.

## Chat

- Stop really stops, and the stopped reply doesn't come back on reload. It still says
  "Stopped" after a restart (#6537).
- A path with a space links as one path (#6536).
- The Context meter uses the model's real window. Cloud models have up to 1M tokens (#6538).
- An empty answer doesn't blame Lemonade when Lemonade is fine.
- A triple Enter makes one chat, not three.
- Pasted line breaks and Windows backslashes survive. A long paste is seen in full by
  follow-ups, not just the first 2,000 characters.
- A pending permission prompt doesn't block other chats. "Add to this reply" and
  "Always allow" work.
- Waiting on "Waiting for the GPU — 1st in line" clears. A startup job or goal once
  held the turn slot.
- Auto-scroll follows a running answer but doesn't yank the view while you scroll up
  to read.

## Agent and files

- Files in GAIA's own folders are found.
- Non-image pastes are kept.
- CP1252 and other non-UTF-8 text files are readable.
- Document Q&A answers from the document. Check with a planted fact and
  `app_state.py grep`.
- The Context meter matches what was sent.

## Visual and language

- The composer focus border is thin. A thick border was "fixed" once without anyone
  looking at the rebuilt app.
- English and Arabic, light and dark, at 390 px phone width. Rules are in the
  `reviewing-agent-ui` skill.
- New strings from the backend (banners, step lines, errors) are translated.

## Performance

- Time to first token and total answer time against the previous release on the same
  machine. "It can't take 4x longer" was a release blocker. Record the numbers in the
  report, and flag anything about 2x slower.
