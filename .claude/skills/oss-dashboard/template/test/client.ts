/**
 * Runs a page's own client scripts in Node against a minimal stand-in for the DOM, so tests
 * can set the data a page loaded (D) and check what its draw functions put on the page.
 * Requests never resolve: the page stays as the test leaves it.
 */

interface Stub {
  [k: string]: unknown;
  innerHTML: string;
  textContent: string;
}

function stub(): Stub {
  const e: Stub = {
    innerHTML: "",
    textContent: "",
    hidden: false,
    value: "",
    checked: false,
    length: 0,
    dataset: {},
    style: {},
    clientWidth: 800,
    offsetWidth: 0,
    offsetHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {},
    getAttribute: () => null,
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    focus() {},
    remove() {},
    closest: () => null,
    querySelector: () => stub(),
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, bottom: 0 }),
  };
  return e;
}

/** Evaluates an expression inside the page's script scope (its functions and `var`s). */
export type PageScope = <T = unknown>(expr: string) => T;

export function runPage(html: string, search = "?range=30"): PageScope & { el: (id: string) => Stub } {
  const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]!).join("\n;\n");
  const els = new Map<string, Stub>();
  const el = (id: string) => els.get(id) ?? els.set(id, stub()).get(id)!;
  const document = {
    getElementById: el,
    querySelector: () => stub(),
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => stub(),
    createElementNS: () => stub(),
    documentElement: { dataset: {} },
  };
  const env = {
    document,
    window: { addEventListener() {} },
    location: { search },
    history: { replaceState() {} },
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false }),
    fetch: () => new Promise(() => {}),
    MutationObserver: class {
      observe() {}
    },
  };
  const scope = new Function(...Object.keys(env), `${code}\n;return function(expr){return eval(expr);};`)(...Object.values(env)) as PageScope;
  return Object.assign(scope, { el });
}
