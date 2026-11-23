// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Right-click menu for the app window. Electron shows none by default, and on
 * Windows and Linux the window has no Edit menu either. Role items take their
 * labels from the OS; the rest are English, like the tray menu.
 */

const MAX_SUGGESTIONS = 5;

/**
 * The menu template for one right-click, or [] when there is nothing to offer.
 * `params` is Electron's context-menu ContextMenuParams.
 */
function buildContextMenuTemplate(params, actions) {
  const sections = [];

  if (params.isEditable) {
    if (params.misspelledWord) {
      const spelling = params.dictionarySuggestions
        .slice(0, MAX_SUGGESTIONS)
        .map((word) => ({ label: word, click: () => actions.replaceMisspelling(word) }));
      spelling.push({
        label: "Add to dictionary",
        click: () => actions.addToDictionary(params.misspelledWord),
      });
      sections.push(spelling);
    }
    const flags = params.editFlags;
    sections.push([
      { role: "cut", enabled: flags.canCut },
      { role: "copy", enabled: flags.canCopy },
      { role: "paste", enabled: flags.canPaste },
      { type: "separator" },
      { role: "selectAll", enabled: flags.canSelectAll },
    ]);
  } else if (params.selectionText && params.selectionText.trim()) {
    sections.push([{ role: "copy" }]);
  }

  if (params.linkURL) {
    sections.push([{ label: "Copy link address", click: () => actions.copyLink(params.linkURL) }]);
  }
  if (params.mediaType === "image" && params.hasImageContents) {
    sections.push([{ label: "Copy image", click: () => actions.copyImageAt(params.x, params.y) }]);
  }

  return sections.flatMap((items, i) => (i === 0 ? items : [{ type: "separator" }, ...items]));
}

/** Show the menu on right-click in `window`'s own webContents. */
function installContextMenu(window, { Menu, clipboard }) {
  const wc = window.webContents;
  wc.on("context-menu", (_event, params) => {
    const template = buildContextMenuTemplate(params, {
      replaceMisspelling: (word) => wc.replaceMisspelling(word),
      addToDictionary: (word) => wc.session.addWordToSpellCheckerDictionary(word),
      copyLink: (url) => clipboard.writeText(url),
      copyImageAt: (x, y) => wc.copyImageAt(x, y),
    });
    if (template.length === 0) return;
    Menu.buildFromTemplate(template).popup({ window });
  });
}

module.exports = { buildContextMenuTemplate, installContextMenu, MAX_SUGGESTIONS };
