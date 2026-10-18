"""Generate src/styles/palettes.css: alternative color palettes for the Agent UI.

    python scripts/gen_palettes.py src/styles/palettes.css

Each palette re-tints the neutral and accent tokens of index.css in light and
dark; status colors, code surfaces and shadows stay as they are. Text tokens
are nudged until they clear 4.5:1 on every surface they sit on.
"""

import sys

PALETTES = {
    # id: (name, light{bg, side, text, muted, accent_solid, accent_text}, dark{...})
    "stone": (
        "Stone and ink",
        dict(
            bg="#FAFAF9",
            side="#F2F2F0",
            text="#1C1C1A",
            muted="#5F5F5B",
            solid="#2B2B29",
            atext="#2B2B29",
        ),
        dict(
            bg="#161615",
            side="#121211",
            text="#ECECE9",
            muted="#A3A39E",
            solid="#E4E4E0",
            atext="#ECECE9",
        ),
    ),
    "marble": (
        "Blue marble",
        dict(
            bg="#F8FAFC",
            side="#EFF3F7",
            text="#16202C",
            muted="#556270",
            solid="#1F5FA8",
            atext="#1A5294",
        ),
        dict(
            bg="#0F151C",
            side="#0B1117",
            text="#E7EDF3",
            muted="#9AA8B7",
            solid="#5B9BE6",
            atext="#8EBCF0",
        ),
    ),
    "glacier": (
        "Glacier",
        dict(
            bg="#F7FAFA",
            side="#EDF3F4",
            text="#15211F",
            muted="#536464",
            solid="#2C6E78",
            atext="#225A62",
        ),
        dict(
            bg="#0F1617",
            side="#0B1112",
            text="#E5EEEE",
            muted="#98AAAB",
            solid="#5FB4BF",
            atext="#8ACDD5",
        ),
    ),
    "forest": (
        "Forest floor",
        dict(
            bg="#F8F9F7",
            side="#EFF2EE",
            text="#18201A",
            muted="#56625A",
            solid="#3A6B48",
            atext="#2F5A3C",
        ),
        dict(
            bg="#121613",
            side="#0E120F",
            text="#E6ECE6",
            muted="#9EAA9F",
            solid="#7DBA8C",
            atext="#A2D0AD",
        ),
    ),
    "sage": (
        "Sage and mist",
        dict(
            bg="#F9F9F7",
            side="#F0F1EE",
            text="#1D201B",
            muted="#5C6058",
            solid="#5E7A63",
            atext="#4B6450",
        ),
        dict(
            bg="#141513",
            side="#101110",
            text="#E8EAE5",
            muted="#A2A69D",
            solid="#9BB5A0",
            atext="#B8CDBC",
        ),
    ),
    "basalt": (
        "Basalt and lichen",
        dict(
            bg="#F8F8F6",
            side="#EFEFEC",
            text="#1B1B19",
            muted="#5C5C57",
            solid="#6B7A2E",
            atext="#566324",
        ),
        dict(
            bg="#141413",
            side="#10100F",
            text="#EBEBE7",
            muted="#A3A39C",
            solid="#B4C36A",
            atext="#CAD58F",
        ),
    ),
    "gold": (
        "Graphite and gold",
        dict(
            bg="#F7F7F8",
            side="#EFEFF1",
            text="#18181B",
            muted="#5A5A62",
            solid="#D9A300",
            atext="#7A5800",
        ),
        dict(
            bg="#141416",
            side="#101012",
            text="#ECECEE",
            muted="#A0A0A8",
            solid="#F2C230",
            atext="#F2C230",
        ),
    ),
    "dusk": (
        "Dusk",
        dict(
            bg="#F9F9FB",
            side="#F0F0F5",
            text="#1C1C27",
            muted="#5C5C6B",
            solid="#4F5A9C",
            atext="#424C85",
        ),
        dict(
            bg="#13131A",
            side="#0F0F15",
            text="#E9E9F0",
            muted="#A2A2B2",
            solid="#97A1E0",
            atext="#B5BCEB",
        ),
    ),
}


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def hexs(c):
    return "#" + "".join(f"{max(0, min(255, round(v))):02X}" for v in c)


def mix(a, b, t):
    """t of b mixed into a."""
    ca, cb = rgb(a), rgb(b)
    return hexs(tuple(x + (y - x) * t for x, y in zip(ca, cb)))


def lum(h):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure(fg, bgs, target, toward):
    """Move fg toward `toward` until it clears target on every bg."""
    t = 0.0
    out = fg
    while min(contrast(out, b) for b in bgs) < target and t < 1:
        t += 0.02
        out = mix(fg, toward, t)
    return out


def rgba(h, a):
    r, g, b = rgb(h)
    return f"rgba({r}, {g}, {b}, {a})"


def tokens(p, dark):
    bg, side, text = p["bg"], p["side"], p["text"]
    card = mix(bg, "#FFFFFF", 0.0) if dark else "#FFFFFF"
    if dark:
        card = mix(bg, text, 0.06)
    hover = mix(bg, text, 0.06 if not dark else 0.09)
    active = mix(bg, text, 0.10 if not dark else 0.14)
    tertiary = mix(bg, text, 0.07 if not dark else 0.11)
    secondary = mix(bg, side, 0.5)
    recessed = mix(bg, side, 0.8) if not dark else mix(bg, "#000000", 0.15)
    surfaces = [bg, side, card, hover, secondary, tertiary, recessed]
    far = "#000000" if not dark else "#FFFFFF"
    muted = ensure(p["muted"], surfaces + [active], 4.6, far)
    border = mix(bg, text, 0.26 if not dark else 0.24)
    border = ensure(border, [bg, card, side], 1.6, far)
    solid = ensure(p["solid"], [bg], 3.0, far)
    atext = ensure(p["atext"], [bg, card, side], 4.6, far)
    fill = mix(card, solid, 0.14 if not dark else 0.22)
    fill_hover = mix(card, solid, 0.22 if not dark else 0.30)
    fill_text = ensure(atext, [fill, fill_hover], 4.6, far)
    on_solid = "#FFFFFF" if contrast("#FFFFFF", solid) >= contrast(bg, solid) else bg
    accent_light = mix(solid, "#FFFFFF" if not dark else "#FFFFFF", 0.18)
    return {
        "--bg-primary": bg,
        "--bg-secondary": secondary,
        "--bg-tertiary": tertiary,
        "--bg-sidebar": side,
        "--bg-input": card,
        "--bg-card": card,
        "--bg-hover": hover,
        "--bg-active": active,
        "--bg-user-msg": card,
        "--bg-recessed": recessed,
        "--text-primary": text,
        "--text-secondary": muted,
        "--text-muted": muted,
        "--border": border,
        "--border-light": border,
        "--border-recessed": border,
        "--accent": atext,
        "--accent-light": accent_light,
        "--accent-solid": solid,
        "--accent-dim": rgba(solid, 0.16),
        "--accent-dim2": rgba(solid, 0.08),
        "--accent-selection": rgba(solid, 0.26),
        "--accent-ring": rgba(solid, 0.38),
        "--accent-fill": fill,
        "--accent-fill-text": fill_text,
        "--accent-fill-hover": fill_hover,
        "--tint-subtle": rgba(text, 0.03),
        "--tint-hover": rgba(text, 0.05 if not dark else 0.06),
        **({"--text-inverse": bg, "--switch-knob": text} if dark else {}),
    }


def block(sel, toks):
    body = "\n".join(f"    {k}: {v};" for k, v in toks.items())
    return f"{sel} {{\n{body}\n}}\n"


out = ["""/* Alternative color palettes, chosen in Settings > General > Colors.
 * The default look is index.css itself; each palette here re-tints its
 * neutral and accent tokens in both themes. Status colors, code surfaces and
 * shadows are shared. Generated by scripts/gen_palettes.py; palettes.test.ts holds
 * every text token to 4.5:1 on the surfaces it sits on. */
"""]
for pid, (name, light, dark) in PALETTES.items():
    out.append(f"/* {name} */\n")
    out.append(block(f':root[data-palette="{pid}"]', tokens(light, False)))
    out.append(
        block(f':root[data-palette="{pid}"][data-theme="dark"]', tokens(dark, True))
    )
open(sys.argv[1], "w", encoding="utf-8", newline="\n").write("\n".join(out))
print("wrote", sys.argv[1])
