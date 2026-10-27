// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { renderEvalsPage } from "../src/evalspage";
import { renderCiPage, renderDevelopersPage, renderUsersPage } from "../src/page";

/** The client script of a page; a syntax error there leaves the page blank in the browser. */
const scriptOf = (html: string) => html.split("<script>").pop()!.split("</script>")[0]!;

describe("page scripts", () => {
  const pages: [string, string][] = [
    ["users", renderUsersPage()],
    ["developers", renderDevelopersPage(["alice", "bob-work"])],
    ["ci", renderCiPage()],
    ["evals", renderEvalsPage()],
  ];

  it.each(pages)("%s page script parses", (_name, html) => {
    expect(() => new Function(scriptOf(html))).not.toThrow();
  });

  it.each(pages)("%s page has no unfilled placeholders", (_name, html) => {
    expect(html).not.toMatch(/__[A-Z]+__/);
  });
});
