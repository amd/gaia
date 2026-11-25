// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Tests for the app window's right-click menu (services/context-menu.cjs). */

const {
  buildContextMenuTemplate,
  installContextMenu,
  menuLabels,
  MAX_SUGGESTIONS,
} = require("../../src/gaia/apps/webui/services/context-menu.cjs");

const allEdits = { canCut: true, canCopy: true, canPaste: true, canSelectAll: true, canUndo: false, canRedo: false, canDelete: true, canEditRichly: false };
const noEdits = { ...allEdits, canCut: false, canCopy: false, canDelete: false };

function params(overrides = {}) {
  return {
    x: 10,
    y: 20,
    linkURL: "",
    linkText: "",
    srcURL: "",
    mediaType: "none",
    hasImageContents: false,
    isEditable: false,
    selectionText: "",
    misspelledWord: "",
    dictionarySuggestions: [],
    editFlags: noEdits,
    ...overrides,
  };
}

function makeActions() {
  return {
    replaceMisspelling: jest.fn(),
    addToDictionary: jest.fn(),
    copyLink: jest.fn(),
    copyImageAt: jest.fn(),
  };
}

const shape = (template) => template.map((i) => i.role || i.label || i.type);

describe("buildContextMenuTemplate", () => {
  test("a misspelled word in a field offers fixes, Add to dictionary, then the edit items", () => {
    const actions = makeActions();
    const template = buildContextMenuTemplate(
      params({ isEditable: true, misspelledWord: "teh", dictionarySuggestions: ["the", "ten"], editFlags: allEdits }),
      actions
    );
    expect(shape(template)).toEqual(["the", "ten", "Add to dictionary", "separator", "cut", "copy", "paste", "separator", "selectAll"]);

    template[0].click();
    expect(actions.replaceMisspelling).toHaveBeenCalledWith("the");
    template[2].click();
    expect(actions.addToDictionary).toHaveBeenCalledWith("teh");
  });

  test("caps the spelling suggestions", () => {
    const words = ["a", "b", "c", "d", "e", "f", "g"];
    const template = buildContextMenuTemplate(
      params({ isEditable: true, misspelledWord: "x", dictionarySuggestions: words, editFlags: allEdits }),
      makeActions()
    );
    expect(template.filter((i) => words.includes(i.label))).toHaveLength(MAX_SUGGESTIONS);
  });

  test("a field with nothing selected keeps Cut and Copy disabled and Paste enabled", () => {
    const template = buildContextMenuTemplate(params({ isEditable: true, editFlags: noEdits }), makeActions());
    expect(shape(template)).toEqual(["cut", "copy", "paste", "separator", "selectAll"]);
    const enabled = Object.fromEntries(template.filter((i) => i.role).map((i) => [i.role, i.enabled]));
    expect(enabled).toEqual({ cut: false, copy: false, paste: true, selectAll: true });
  });

  test("selected text outside a field offers only Copy", () => {
    const template = buildContextMenuTemplate(params({ selectionText: "an answer" }), makeActions());
    expect(shape(template)).toEqual(["copy"]);
  });

  test("a link offers Copy link address", () => {
    const actions = makeActions();
    const template = buildContextMenuTemplate(params({ linkURL: "https://amd-gaia.ai/docs", linkText: "docs" }), actions);
    expect(shape(template)).toEqual(["Copy link address"]);
    template[0].click();
    expect(actions.copyLink).toHaveBeenCalledWith("https://amd-gaia.ai/docs");
  });

  test("selected link text offers Copy, then Copy link address", () => {
    const template = buildContextMenuTemplate(
      params({ selectionText: "docs", linkURL: "https://amd-gaia.ai/docs" }),
      makeActions()
    );
    expect(shape(template)).toEqual(["copy", "separator", "Copy link address"]);
  });

  test("an image offers Copy image at the clicked point", () => {
    const actions = makeActions();
    const template = buildContextMenuTemplate(
      params({ mediaType: "image", hasImageContents: true, srcURL: "blob:x" }),
      actions
    );
    expect(shape(template)).toEqual(["Copy image"]);
    template[0].click();
    expect(actions.copyImageAt).toHaveBeenCalledWith(10, 20);
  });

  test("an image that failed to load offers nothing", () => {
    expect(buildContextMenuTemplate(params({ mediaType: "image", hasImageContents: false }), makeActions())).toEqual([]);
  });

  test.each([
    ["a plain page", params()],
    ["whitespace-only selection", params({ selectionText: "  \n" })],
  ])("%s gets no menu", (_name, p) => {
    expect(buildContextMenuTemplate(p, makeActions())).toEqual([]);
  });
});

describe("installContextMenu", () => {
  function setup(locale) {
    const handlers = {};
    const wc = {
      on: jest.fn((event, fn) => { handlers[event] = fn; }),
      replaceMisspelling: jest.fn(),
      copyImageAt: jest.fn(),
      session: { addWordToSpellCheckerDictionary: jest.fn() },
    };
    const window = { webContents: wc };
    const popup = jest.fn();
    const Menu = { buildFromTemplate: jest.fn(() => ({ popup })) };
    const clipboard = { writeText: jest.fn() };
    installContextMenu(window, { Menu, clipboard, locale });
    return { handlers, wc, window, Menu, popup, clipboard };
  }

  test("pops the menu over the window and wires its actions to the page", () => {
    const { handlers, wc, window, Menu, popup, clipboard } = setup();
    handlers["context-menu"]({}, params({
      isEditable: true, misspelledWord: "teh", dictionarySuggestions: ["the"], editFlags: allEdits,
    }));
    expect(popup).toHaveBeenCalledWith({ window });
    const template = Menu.buildFromTemplate.mock.calls[0][0];
    template[0].click();
    expect(wc.replaceMisspelling).toHaveBeenCalledWith("the");
    template[1].click();
    expect(wc.session.addWordToSpellCheckerDictionary).toHaveBeenCalledWith("teh");

    handlers["context-menu"]({}, params({ linkURL: "https://amd-gaia.ai" }));
    Menu.buildFromTemplate.mock.calls[1][0][0].click();
    expect(clipboard.writeText).toHaveBeenCalledWith("https://amd-gaia.ai");
  });

  test("shows nothing where there is nothing to offer", () => {
    const { handlers, Menu, popup } = setup();
    handlers["context-menu"]({}, params());
    expect(Menu.buildFromTemplate).not.toHaveBeenCalled();
    expect(popup).not.toHaveBeenCalled();
  });

  test("an Arabic OS gets Arabic labels on the custom items, like the OS-labelled ones", () => {
    const { handlers, Menu } = setup("ar-EG");
    handlers["context-menu"]({}, params({
      isEditable: true, misspelledWord: "teh", dictionarySuggestions: [], editFlags: allEdits,
      linkURL: "https://amd-gaia.ai", mediaType: "image", hasImageContents: true,
    }));
    const labels = Menu.buildFromTemplate.mock.calls[0][0].map((i) => i.label).filter(Boolean);
    expect(labels).toEqual(["إضافة إلى القاموس", "نسخ عنوان الرابط", "نسخ الصورة"]);
  });
});

describe("menuLabels", () => {
  test.each([
    ["ar", "نسخ الصورة"],
    ["ar-SA", "نسخ الصورة"],
    ["AR_eg", "نسخ الصورة"],
    ["en-US", "Copy image"],
    ["fr", "Copy image"],
    ["", "Copy image"],
    [undefined, "Copy image"],
  ])("%s → %s", (locale, copyImage) => {
    expect(menuLabels(locale).copyImage).toBe(copyImage);
  });
});
