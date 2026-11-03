// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The client-side table sort, run in Node against the exact functions the pages ship. */

import { describe, expect, it } from "vitest";
import { renderDevelopersPage } from "../src/page";

const script = renderDevelopersPage().split("<script>").pop()!.split("</script>")[0]!;

/** Source of a top-level client function: from its declaration to the next top-level statement. */
const fn = (name: string) => {
  const start = script.indexOf(`\nfunction ${name}(`);
  if (start < 0) throw new Error(`client function ${name} not found`);
  const rest = script.slice(start + 1);
  const end = rest.slice(1).search(/\n(?:function |var |new |\/\*|document\.)/);
  return end < 0 ? rest : rest.slice(0, end + 1);
};

const { cellKey, sortRows, tableView } = new Function(
  `var SORT={},UNIT={s:1,min:60,h:3600,d:86400,day:86400,days:86400,w:604800,week:604800,weeks:604800,k:1e3,M:1e6};
   ${fn("esc")}\n${fn("cellKey")}\n${fn("sortRows")}\n${fn("tableView")}
   return {cellKey:cellKey,sortRows:sortRows,tableView:tableView};`,
)() as {
  cellKey: (td: unknown) => unknown;
  sortRows: (t: unknown, ci: number, d: number) => void;
  tableView: (head: string[], rows: unknown[][]) => string;
};

const td = (text: string, v?: string) => ({ textContent: text, getAttribute: (n: string) => (n === "data-v" ? (v ?? null) : null) });

/** Sorts a table built from tableView's HTML by one column and returns that column's text, top to bottom. */
function sortedColumn(html: string, ci: number, d: number): string[] {
  const head = { cells: [] as { setAttribute: () => void }[] };
  type Row = { _i: number; cells: ReturnType<typeof td>[]; parentNode: { appendChild: (r: Row) => void } };
  const body: Row[] = [];
  const order: Row[] = [];
  const parentNode = { appendChild: (r: Row) => void order.push(r) };
  for (const tr of html.split("<tr>").slice(1)) {
    const cells = [...tr.matchAll(/<t([hd])([^>]*)>(.*?)<\/t[hd]>/g)];
    if (cells[0]![1] === "h") {
      head.cells = cells.map(() => ({ setAttribute: () => {} }));
      continue;
    }
    body.push({ _i: body.length, parentNode, cells: cells.map((c) => td(c[3]!, /data-v="([^"]*)"/.exec(c[2]!)?.[1])) });
  }
  sortRows({ rows: [head, ...body] }, ci, d);
  return order.map((r) => r.cells[ci]!.textContent);
}

describe("chart table view", () => {
  const days = ["2026-10-29", "2026-04-02", "2026-08-15", "2025-12-31"];
  const label = (k: string) => new Date(`${k}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

  it("puts the ISO key on the date cell, escaped, and plain cells stay plain", () => {
    const html = tableView(["Day", "Stars"], [[{ h: "Oct 29, 2026", v: '2026-10-29"><x' }, "5"]]);
    expect(html).toContain('<td data-v="2026-10-29&quot;&gt;&lt;x">Oct 29, 2026</td><td class="n">5</td>');
  });

  it("sorts dates by time, not by month name", () => {
    const html = tableView(["Day", "Stars"], days.map((k) => [{ h: label(k), v: k }, "1"]));
    expect(sortedColumn(html, 0, 1)).toEqual(["Dec 31, 2025", "Apr 2, 2026", "Aug 15, 2026", "Oct 29, 2026"]);
    expect(sortedColumn(html, 0, -1)).toEqual(["Oct 29, 2026", "Aug 15, 2026", "Apr 2, 2026", "Dec 31, 2025"]);
  });

  it("sorts month keys within a year instead of tying them on the year", () => {
    const months = ["2026-10", "2026-02", "2025-11", "2026-09"];
    const html = tableView(["Month", "Stars"], months.map((k) => [{ h: k, v: k }, "1"]));
    expect(sortedColumn(html, 0, 1)).toEqual(["2025-11", "2026-02", "2026-09", "2026-10"]);
  });

  it("is what the Developers page draws for a dated chart", () => {
    expect(fn("draw")).toContain("{h:esc(longLab(k,spec.gran).replace('Week of ','')),v:k}");
  });
});

describe("cell sort keys", () => {
  it("reads ±0 as zero and new as lower than any change", () => {
    expect(cellKey(td("±0"))).toBe(0);
    const order = ["+5 pts", "new", "±0", "-12 pts", "−3 pts"]
      .map((t) => [t, cellKey(td(t)) as number] as const)
      .sort((a, b) => a[1] - b[1])
      .map((x) => x[0]);
    expect(order).toEqual(["new", "-12 pts", "−3 pts", "±0", "+5 pts"]);
  });

  it("sorts pass counts by their rate, not by the passed count", () => {
    expect(cellKey(td("3/7"))).toBeCloseTo(3 / 7);
    expect(cellKey(td("9/10"))).toBeGreaterThan(cellKey(td("3/4")) as number);
    expect(cellKey(td("1,200/2,400"))).toBe(0.5);
    expect(cellKey(td("0/0"))).toBeNull();
  });

  it("leaves token pairs and other text alone", () => {
    expect(cellKey(td("950 / 120"))).toBe(950);
    expect(cellKey(td("12k / 3k"))).toBe(12000);
    expect(cellKey(td("92% (3/7)"))).toBe(92);
    expect(cellKey(td("newcomer"))).toBe("newcomer");
    expect(cellKey(td("—"))).toBeNull();
  });
});
