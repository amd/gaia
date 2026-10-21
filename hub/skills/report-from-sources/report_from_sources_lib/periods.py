# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read period labels the way spreadsheets write them.

"2026Q2", "Q2 2026", "2Q26", "second quarter 2026", "Jun-2026", "2026-06",
"06/30/2026", a ``datetime`` cell, "FY2026", "FY 2025/26", "2025-26", "H1 2026"
and a bare year all become a :class:`Period` with one canonical label and a
position on a common time line, so columns and rows from different files line
up and sort correctly.

Nothing here guesses silently: a day/month order that cannot be told apart is
resolved for a whole axis at once (:func:`parse_axis`), and an axis that mixes
frequencies keeps them apart and says so.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Frequencies, coarsest first. ``FY``/``FQ`` are fiscal: their calendar dates
#: are unknown, so their positions only order them among themselves and
#: approximately against calendar periods.
FREQ_NAMES = {
    "A": "annual",
    "FY": "fiscal year",
    "H": "half-year",
    "FQ": "fiscal quarter",
    "Q": "quarterly",
    "M": "monthly",
    "D": "daily/dated",
}

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_RE = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_ORDINAL_Q = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
}
_Y = r"(?P<y>(?:19|20)\d{2}|'?\d{2})"
_Y4 = r"(?P<y>(?:19|20)\d{2})"
_SEP = r"\s*[-_/ :.,']?\s*"

#: A footnote marker or status flag stuck to the end of a label: "2026Q2(p)",
#: "Jun-2026*", "2025 (r)", "2026¹".
_TRAILING_MARKER = re.compile(
    r"(?:\s*\((?:[a-z]|\d{1,2}|[pre]|est|prelim|rev)\)|\s*\[[a-z0-9]{1,3}\]|"
    r"\s*[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]+)+$",
    re.IGNORECASE,
)

_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Fiscal quarters before calendar quarters: "FY26 Q1" must not read as Q1 of 26.
    ("FQ", re.compile(rf"^fy\s*'?{_Y}{_SEP}q(?P<q>[1-4])$", re.I)),
    ("FQ", re.compile(rf"^q(?P<q>[1-4]){_SEP}fy\s*'?{_Y}$", re.I)),
    ("Q", re.compile(rf"^{_Y4}{_SEP}q(?P<q>[1-4])$", re.I)),
    ("Q", re.compile(rf"^q(?P<q>[1-4]){_SEP}{_Y}$", re.I)),
    ("Q", re.compile(rf"^(?P<q>[1-4])q{_SEP}{_Y}$", re.I)),
    ("Q", re.compile(rf"^{_Y4}{_SEP}(?P<q>[1-4])q$", re.I)),
    (
        "Q",
        re.compile(
            rf"^(?P<qw>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+quarter"
            rf"(?:\s+of)?{_SEP}{_Y4}$",
            re.I,
        ),
    ),
    ("H", re.compile(rf"^{_Y4}{_SEP}h(?P<h>[12])$", re.I)),
    ("H", re.compile(rf"^h(?P<h>[12]){_SEP}{_Y}$", re.I)),
    ("H", re.compile(rf"^(?P<h>[12])h{_SEP}{_Y}$", re.I)),
    # "FY 2025/26", "FY2025-2026"
    (
        "FY2",
        re.compile(
            r"^(?:fy|fiscal(?:\s+year)?)\s*'?(?P<y1>(?:19|20)\d{2})\s*[-/]\s*(?P<y2>\d{2}|\d{4})$",
            re.I,
        ),
    ),
    ("FY", re.compile(rf"^(?:fy|fiscal(?:\s+year)?)\s*[-']?\s*{_Y}$", re.I)),
    # "2025/26", "2025-2026": a span of two consecutive years is a fiscal year.
    ("FY2", re.compile(r"^(?P<y1>(?:19|20)\d{2})\s*[-/]\s*(?P<y2>\d{2}|\d{4})$")),
    ("A", re.compile(rf"^(?:cy|year|calendar\s+year)?\s*{_Y4}$", re.I)),
    ("M", re.compile(rf"^{_MONTH_RE}{_SEP}{_Y}$", re.I)),
    ("M", re.compile(rf"^{_Y4}{_SEP}{_MONTH_RE}$", re.I)),
    ("M", re.compile(rf"^{_Y4}\s*m(?P<m>\d{{1,2}})$", re.I)),
    ("M", re.compile(rf"^{_Y4}[-/.](?P<m>\d{{1,2}})$")),
    ("M", re.compile(rf"^(?P<m>\d{{1,2}})[-/.]{_Y4}$")),
    (
        "D",
        re.compile(
            rf"^{_Y4}[-/.](?P<m>\d{{1,2}})[-/.](?P<d>\d{{1,2}})"
            r"(?:[t ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?$",
            re.I,
        ),
    ),
    (
        "DMY",
        re.compile(
            r"^(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>(?:19|20)\d{2}|\d{2})"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"
        ),
    ),
    ("D", re.compile(rf"^(?P<d>\d{{1,2}}){_SEP}{_MONTH_RE}{_SEP}{_Y4}$", re.I)),
    (
        "D",
        re.compile(
            rf"^{_MONTH_RE}\s*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s*{_Y4}$", re.I
        ),
    ),
]


@dataclass(frozen=True)
class Period:
    """One period: a quarter, month, year, fiscal year, half or dated day.

    ``position`` is the period's end as a fractional year (Q2 2026 -> 2026.5,
    June 2026 -> 2026.5, 2026 -> 2027.0), so periods of different frequency
    order on one line. ``label`` is canonical: ``2026Q2``, ``2026-06``,
    ``2026``, ``FY2026``, ``FY2026Q1``, ``2026H1``, ``2026-06-30``.
    """

    freq: str
    year: int
    sub: int = 0
    day: Optional[date] = None

    @property
    def label(self) -> str:
        if self.freq == "A":
            return f"{self.year}"
        if self.freq == "FY":
            return f"FY{self.year}"
        if self.freq == "FQ":
            return f"FY{self.year}Q{self.sub}"
        if self.freq == "H":
            return f"{self.year}H{self.sub}"
        if self.freq == "Q":
            return f"{self.year}Q{self.sub}"
        if self.freq == "M":
            return f"{self.year}-{self.sub:02d}"
        return self.day.isoformat() if self.day else f"{self.year}"

    @property
    def position(self) -> float:
        if self.freq in ("A", "FY"):
            return float(self.year + 1)
        if self.freq in ("Q", "FQ"):
            return self.year + self.sub / 4
        if self.freq == "H":
            return self.year + self.sub / 2
        if self.freq == "M":
            return self.year + self.sub / 12
        if self.day is not None:
            days = 366 if calendar.isleap(self.day.year) else 365
            return self.day.year + self.day.timetuple().tm_yday / days
        return float(self.year)

    @property
    def start_position(self) -> float:
        """Where the period begins on the same line as :attr:`position`."""
        width = {"A": 1.0, "FY": 1.0, "H": 0.5, "Q": 0.25, "FQ": 0.25, "M": 1 / 12}
        if self.freq in width:
            return self.position - width[self.freq]
        return self.position - 1 / 366

    @property
    def sort_key(self) -> Tuple[float, int]:
        order = ["A", "FY", "H", "FQ", "Q", "M", "D"]
        return (self.position, order.index(self.freq))

    def __str__(self) -> str:
        return self.label


def _year(text: str) -> int:
    text = text.lstrip("'")
    value = int(text)
    if len(text) == 2:
        return 2000 + value if value < 70 else 1900 + value
    return value


def _month(text: str) -> int:
    return _MONTHS[text.lower().rstrip(".")[:3]]


def _valid_day(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def clean_label(text: str) -> str:
    """The label with footnote markers and surrounding noise removed."""
    text = str(text).replace(" ", " ").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return _TRAILING_MARKER.sub("", text).strip()


def parse_period(
    value: Any, *, bare_year_numbers: bool = False, dayfirst: Optional[bool] = None
) -> Optional[Period]:
    """Parse one cell or label into a :class:`Period`, or None if it is not one.

    Args:
        value: A string, ``datetime``/``date``, or (with ``bare_year_numbers``)
            an integer such as 2026 from a header row.
        bare_year_numbers: Read an integral number 1900-2100 as a year. Only
            safe in header rows and label columns, never for data cells.
        dayfirst: For "03/04/2026": True reads 3 April, False reads 4 March,
            None reads month-first unless the first number is over 12.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return Period("D", value.year, 0, value.date())
    if isinstance(value, date):
        return Period("D", value.year, 0, value)
    if isinstance(value, (int, float)):
        if bare_year_numbers and float(value).is_integer() and 1900 <= value <= 2100:
            return Period("A", int(value))
        return None
    text = clean_label(str(value))
    if not text or len(text) > 40:
        return None
    for kind, pattern in _PATTERNS:
        match = pattern.match(text)
        if match:
            period = _from_match(kind, match.groupdict(), dayfirst)
            if period is not None:
                return period
    return None


def _from_match(
    kind: str, g: Dict[str, Any], dayfirst: Optional[bool]
) -> Optional[Period]:
    """Build the period one pattern matched, or None if its numbers are invalid."""
    if kind in ("Q", "FQ"):
        q = int(g["q"]) if g.get("q") else _ORDINAL_Q[g["qw"].lower()]
        return Period(kind, _year(g["y"]), q)
    if kind == "H":
        return Period("H", _year(g["y"]), int(g["h"]))
    if kind == "FY2":
        y1, y2 = int(g["y1"]), int(g["y2"])
        if y2 < 100:
            y2 += (y1 // 100) * 100
        return Period("FY", y2) if y2 == y1 + 1 else None
    if kind == "FY":
        return Period("FY", _year(g["y"]))
    if kind == "A":
        return Period("A", int(g["y"]))
    if kind == "M":
        m = int(g["m"]) if g.get("m") else _month(g["mon"])
        return Period("M", _year(g["y"]), m) if 1 <= m <= 12 else None
    if kind == "D":
        m = int(g["m"]) if g.get("m") else _month(g["mon"])
        day = _valid_day(int(g["y"]), m, int(g["d"]))
        return Period("D", day.year, 0, day) if day else None
    if kind == "DMY":
        a, b, y = int(g["a"]), int(g["b"]), _year(g["y"])
        if a > 12 or (dayfirst and b <= 12):
            day = _valid_day(y, b, a)
        else:
            day = _valid_day(y, a, b)
        return Period("D", day.year, 0, day) if day else None
    return None


def is_ambiguous_day_month(value: Any) -> bool:
    """True for "03/04/2026"-style text, where either number could be the month."""
    if not isinstance(value, str):
        return False
    match = re.match(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.]", clean_label(value))
    return bool(match) and int(match.group(1)) <= 12 and int(match.group(2)) <= 12


def detect_dayfirst(values: Iterable[Any]) -> Optional[bool]:
    """Day/month order for a whole axis: True, False, or None if undecidable."""
    first_over_12 = second_over_12 = False
    for value in values:
        if not isinstance(value, str):
            continue
        match = re.match(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}", value)
        if match:
            if int(match.group(1)) > 12:
                first_over_12 = True
            if int(match.group(2)) > 12:
                second_over_12 = True
    if first_over_12 and not second_over_12:
        return True
    if second_over_12 and not first_over_12:
        return False
    return None


def _quarter_end(day: date) -> bool:
    return (day.month, day.day) in ((3, 31), (6, 30), (9, 30), (12, 31))


def harmonize(
    periods: Sequence[Optional[Period]],
) -> Tuple[List[Optional[Period]], List[str]]:
    """Put dated cells on the axis's own frequency when that is unambiguous.

    A month column written partly as "Mar 2024" and partly as "2024-03-31"
    becomes all months; dates that all fall on quarter ends next to quarter
    labels become quarters. Dated cells that would collide (two days in one
    month) are left as dates. Returns the periods and notes on what changed.
    """
    present = [p for p in periods if p is not None]
    notes: List[str] = []
    freqs = {p.freq for p in present}
    dated = [p for p in present if p.freq == "D"]
    if not dated:
        return list(periods), notes
    if freqs == {"D"}:
        return _dates_as_periods(list(periods), dated, notes)
    target = None
    if "M" in freqs and freqs <= {"M", "D"}:
        target = "M"
    elif (
        "Q" in freqs and freqs <= {"Q", "D"} and all(_quarter_end(p.day) for p in dated)
    ):
        target = "Q"
    if target is None:
        return list(periods), notes

    def convert(p: Period) -> Period:
        if target == "M":
            return Period("M", p.day.year, p.day.month)
        return Period("Q", p.day.year, (p.day.month - 1) // 3 + 1)

    keys = [convert(p).label for p in dated]
    if len(set(keys)) != len(keys):
        return list(periods), notes
    out = [convert(p) if p is not None and p.freq == "D" else p for p in periods]
    notes.append(
        f"{len(dated)} dated label(s) read as {FREQ_NAMES[target]} periods to match "
        "the rest of the axis"
    )
    return out, notes


def _month_anchor(day: date) -> bool:
    """First or last day of its month: how monthly data is usually dated."""
    return day.day == 1 or day.day == calendar.monthrange(day.year, day.month)[1]


def date_as_period(day: date, freq: str) -> Optional[Period]:
    """*day* as a period of *freq* when it is a month/quarter/year anchor date."""
    if not _month_anchor(day):
        return None
    if freq == "M":
        return Period("M", day.year, day.month)
    if freq == "Q":
        if (day.day == 1 and day.month in (1, 4, 7, 10)) or (
            day.day > 1 and day.month in (3, 6, 9, 12)
        ):
            return Period("Q", day.year, (day.month - 1) // 3 + 1)
        return None
    if freq == "A":
        if (day.day == 1 and day.month == 1) or (day.day > 1 and day.month == 12):
            return Period("A", day.year)
    return None


def _dates_as_periods(
    periods: List[Optional[Period]], dated: List[Period], notes: List[str]
) -> Tuple[List[Optional[Period]], List[str]]:
    """An all-dates axis whose dates all sit on month starts (or ends) is monthly.

    The same for quarter and year anchors. Returns the axis unchanged when two
    dates fall in one period or fewer than three dates are present.
    """
    days = sorted({p.day for p in dated})
    if len(days) < 3 or not all(_month_anchor(d) for d in days):
        return periods, notes
    starts = all(d.day == 1 for d in days)
    ends = all(d.day > 1 for d in days)
    if not (starts or ends):
        return periods, notes
    for freq in ("A", "Q", "M"):
        mapped = [date_as_period(d, freq) for d in days]
        if all(m is not None for m in mapped) and len({m.label for m in mapped}) == len(
            days
        ):
            out = [
                date_as_period(p.day, freq) if p is not None else None for p in periods
            ]
            notes.append(
                f"dates on {'period starts' if starts else 'period ends'} read as "
                f"{FREQ_NAMES[freq]} periods"
            )
            return out, notes
    return periods, notes


def parse_axis(
    values: Sequence[Any], *, bare_year_numbers: bool = False
) -> Tuple[List[Optional[Period]], List[str]]:
    """Parse a run of labels as one axis: shared day/month order, harmonised dates.

    Returns:
        The periods (None where a value is not a period) and notes for the
        caller to report, e.g. an undecidable day/month order.
    """
    notes: List[str] = []
    dayfirst = detect_dayfirst(values)
    if dayfirst is None and any(is_ambiguous_day_month(v) for v in values):
        notes.append(
            "dates like 03/04/2026 could be day-first or month-first and nothing on "
            "the axis settles it; read as month-first (US)"
        )
    periods = [
        parse_period(v, bare_year_numbers=bare_year_numbers, dayfirst=dayfirst)
        for v in values
    ]
    periods, more = harmonize(periods)
    return periods, notes + more


def dominant_freq(periods: Iterable[Optional[Period]]) -> Optional[str]:
    """The most common frequency (finest wins a tie), or None for no periods."""
    counts: Dict[str, int] = {}
    for p in periods:
        if p is not None:
            counts[p.freq] = counts.get(p.freq, 0) + 1
    if not counts:
        return None
    order = ["A", "FY", "H", "FQ", "Q", "M", "D"]
    return max(counts, key=lambda f: (counts[f], order.index(f)))


# --------------------------------------------------------------------------- #
# Period selection: "last 5", "2024Q1:2026Q2", "2023..2025", "all", a list.
# --------------------------------------------------------------------------- #


class PeriodSpecError(ValueError):
    """A ``periods=`` argument that cannot be read."""


@dataclass(frozen=True)
class PeriodSpec:
    """A parsed ``periods=`` request."""

    kind: str  # "all" | "last" | "range" | "list"
    count: int = 0
    start: Optional[Period] = None
    end: Optional[Period] = None
    items: Tuple[Period, ...] = ()

    def describe(self) -> str:
        if self.kind == "all":
            return "all periods"
        if self.kind == "last":
            return f"last {self.count} periods with data"
        if self.kind == "range":
            return f"{self.start.label if self.start else '...'} to {self.end.label if self.end else '...'}"
        return ", ".join(p.label for p in self.items)


def parse_period_spec(spec: Any) -> PeriodSpec:
    """Read ``periods=``: "all", "last 5", "5", "2024Q1:2026Q2", "2023..2025", a list.

    Raises:
        PeriodSpecError: naming the accepted forms.
    """
    if spec is None or (isinstance(spec, str) and not spec.strip()):
        return PeriodSpec("last", count=12)
    if isinstance(spec, int) and not isinstance(spec, bool):
        if spec < 1:
            raise PeriodSpecError("periods must be at least 1")
        return PeriodSpec("last", count=spec)
    if isinstance(spec, (list, tuple)):
        items = [parse_period(x, bare_year_numbers=True) for x in spec]
        if any(p is None for p in items) or not items:
            bad = [str(x) for x, p in zip(spec, items) if p is None]
            raise PeriodSpecError(
                f"not period labels: {', '.join(bad) or '(empty list)'}"
            )
        return PeriodSpec("list", items=tuple(items))
    text = str(spec).strip()
    low = text.lower()
    if low in ("all", "*", "everything"):
        return PeriodSpec("all")
    match = re.match(
        r"^(?:last|latest|most recent|final)?\s*(\d{1,4})(?:\s*periods?)?$", low
    )
    if match and re.fullmatch(r"(?:19|20)\d{2}", low):
        match = None  # "2025" is a year, not "the last 2025 periods"
    if match:
        count = int(match.group(1))
        if count < 1:
            raise PeriodSpecError("periods must be at least 1")
        return PeriodSpec("last", count=count)
    for sep in ("..", ":", " to ", " - ", " through ", "–"):
        if sep in text:
            left, right = (s.strip() for s in text.split(sep, 1))
            start = parse_period(left, bare_year_numbers=True) if left else None
            end = parse_period(right, bare_year_numbers=True) if right else None
            if (
                (left and start is None)
                or (right and end is None)
                or (not left and not right)
            ):
                break
            return PeriodSpec("range", start=start, end=end)
    if "," in text or ";" in text:
        return parse_period_spec(
            [s.strip() for s in re.split(r"[;,]", text) if s.strip()]
        )
    single = parse_period(text, bare_year_numbers=True)
    if single is not None:
        return PeriodSpec("list", items=(single,))
    raise PeriodSpecError(
        f"Could not read periods={spec!r}. Use 'last 5', 'all', a range such as "
        "'2024Q1:2026Q2' or '2023..2025', or a list such as '2025Q4, 2026Q1'."
    )


def select_periods(
    available: Sequence[Period], spec: PeriodSpec
) -> Tuple[List[Period], List[str]]:
    """The periods of *available* (sorted, unique) that *spec* asks for.

    Returns:
        The selected periods, oldest first, and the labels the spec named that
        are not available (for a list spec).
    """
    ordered = sorted(set(available), key=lambda p: p.sort_key)
    if spec.kind == "all":
        return ordered, []
    if spec.kind == "last":
        return ordered[-spec.count :], []
    if spec.kind == "range":
        lo = spec.start.start_position if spec.start else float("-inf")
        hi = spec.end.position if spec.end else float("inf")
        eps = 1e-9
        return [
            p for p in ordered if p.position > lo + eps and p.position <= hi + eps
        ], []
    by_label = {p.label: p for p in ordered}
    picked, missing = [], []
    for item in spec.items:
        if item.label in by_label:
            picked.append(by_label[item.label])
        elif item.freq == "A":
            # "2025" against a quarterly axis means the periods inside 2025.
            inside = [p for p in ordered if p.year == item.year and p.freq != "A"]
            picked.extend(inside)
            if not inside:
                missing.append(item.label)
        else:
            missing.append(item.label)
    picked = sorted(set(picked), key=lambda p: p.sort_key)
    return picked, missing
