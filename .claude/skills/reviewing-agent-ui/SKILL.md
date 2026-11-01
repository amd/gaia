---
name: reviewing-agent-ui
description: Review the Agent UI by looking at it, in English and Arabic, on the real desktop app when it matters. Use before calling any change to src/gaia/apps/webui, the Electron shell, or user-facing backend strings done, and when asked to test, polish or check the UI. Covers which screens to open, what to look for in a right-to-left layout, the automated checks, and the false passes that let visible bugs ship.
---

# Reviewing the Agent UI

**Green tests and an empty overflow list are not a review.** Open the screen, read
the screenshot, and name what looks wrong before you say it's fine. Arabic layout
bugs once shipped past 2,100 passing tests and a DOM audit because nobody looked: the
audit only asked two questions, and the bugs were answers to questions it never asked.

## 1. Run the checks

```bash
cd src/gaia/apps/webui
npm test            # components
npm run test:rtl    # right-to-left layout rules, no baselines, runs in CI
npm run test:visual # pixel baselines, Windows, local only
```

`test:rtl` lives in `visual/rtl.visual.ts`. **When a review finds a bug it missed, add
the rule there first and watch it fail**, then fix the bug. New screens join its list.
Set `GAIA_VISUAL_CHROMIUM` to a Chrome or Edge binary to skip downloading Chromium.

## 2. Look at every screen you touched, in both languages

Take a screenshot with real data, then open the image and read it. Switch language
in Settings → General. Cover the states, not just the happy one:

- empty, loading, running, failed and long content
- a long filename, a long English model name, a 7-document library
- an open modal over a banner
- light and dark
- phone width (390 px)

## 3. What to look for in Arabic

| Check | Wrong looks like |
|---|---|
| Latin names sit on the label's edge | A filename floating mid-row (`dir="auto"` flips `start`) |
| Latin names truncate at their end | `...en3.6 35B A3B` instead of `Qwen3.6 35B ...` |
| Numbers keep their units | `MB 9.7`; wrap sizes in `<bdi dir="ltr">` |
| English sentences keep their punctuation | `.GAIA stays in the tray`; the string needs `dir="auto"` or translating |
| Icons have a gap and mirror when directional | Icon touching text; an arrow pointing the wrong way |
| Nothing is hard-coded English | A string outside `useT`, including text from the backend |
| Padding and position use logical properties | `margin-left` left behind in a component |
| Window controls blend in | A pale block over the sidebar, or bright controls beside a dim modal |
| The English in brackets is quiet | Same size or weight as the Arabic; check a dark palette too, where primary buttons are mid-tone |

The mirror bites in English: a `dir="auto"` label holding an Arabic filename sits at
the far right of its row unless it is pinned left. Check the English UI with an
Arabic-named file too.

Backend strings are a real source of English in Arabic mode: banners, step lines,
chat titles and error messages. Check them, not just the labels in the locale file.

## 4. Use the real app when the shell is involved

The browser preview has no window controls, tray, native dialogs or installer. Anything
touching those needs the packaged app, and a page screenshot won't show the native
controls either, so capture the desktop or ask the person to confirm them.

To test a build without touching the user's install:

1. Download the `windows-installer` and `gaia-wheel` artifacts from the release branch's
   `build-installers.yml` run, and `gaia-agent-win32-x64` from `build_flagship_sidecar.yml`.
2. Unpack the installer into a scratch folder, and point `USERPROFILE`, `HOME`, `APPDATA`
   and `LOCALAPPDATA` at an empty profile.
3. Set `GAIA_LOCAL_WHEEL` to the wheel and launch `gaia-desktop.exe --remote-debugging-port=9333`.
4. Attach with Playwright's `connectOverCDP`.

**Never put a test window in front of the person without saying so, and never call
something tested in the real app when the run was a browser preview.**

## 5. Report what you saw

List the screens and states you looked at, the languages, and whether it was the
browser preview or the packaged app. Say what you didn't look at. If the person spots
a bug you passed, treat it as a gap in this checklist or in `test:rtl` and close it in
the same change.
