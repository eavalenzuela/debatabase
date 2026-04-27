"""Quality pass: clean up auto-parsed cite_shorts and author_lasts.

Fixes:
- Replace non-breaking spaces (U+00A0) with regular spaces.
- Strip trailing commas and stray punctuation.
- Drop redundant first names ("Lauren Gambino, 6" -> "Gambino 6").
- Manual overrides for the 3 oddball institutional/long cases.
- Apply known typo corrections (Muhammed -> Muhammad).

raw_cite is never modified — only parsed fields.
"""

from __future__ import annotations

import re

from sqlalchemy import text as sqltext

from debatabase.db import engine

# (source_id, new_cite_short, new_author_last, new_author_full_or_None)
# author_full=None means leave alone; "" means clear.
MANUAL = {
    91:  ("Potok 17",                "Potok",                "Mark Potok"),
    80:  ("Broadwater & Edmondson 6", "Broadwater & Edmondson", None),
    111: ("EJSW 09",                 "European Journal of Social Work", None),
    102: ("Peoples et al. 6/9",      "Peoples et al.",        "Steve Peoples et al."),
    69:  ("Gimbel & Muhammad 19",    "Gimbel & Muhammad",     "Gimbel; Craig Muhammad"),
}

# Patterns: simple cleanup applied to all sources
COMMA_NBSP_RE = re.compile(r",[\s ]+")     # ", " or ",\xa0"
NBSP_RE = re.compile(r" ")                  # bare \xa0
TRAILING_PUNCT_RE = re.compile(r"[,;\s ]+$")
ETAL_RE = re.compile(r"\bet\.\s+al\b", re.IGNORECASE)

# Drop a leading firstname when the cite_short looks like "Firstname Lastname, YY"
FIRSTNAME_LASTNAME_RE = re.compile(
    r"^([A-Z][a-z]+)\s+([A-Z][\w'’\-]+(?:\s+(?:&|and|et\s+al\.?)\s+[A-Z][\w'’\-]+)?)\s*[,\s]\s*(\S.*)$"
)


def clean_cite_short(s: str) -> str:
    s = NBSP_RE.sub(" ", s)
    s = COMMA_NBSP_RE.sub(" ", s)
    s = ETAL_RE.sub("et al.", s)
    s = TRAILING_PUNCT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop "and" -> "&" for two-author normalization (only when joining 2 authors before year)
    s = re.sub(r"\b(\w+)\s+and\s+(\w+)\s*,?\s*(\d)", r"\1 & \2 \3", s)
    # If "Firstname Lastname YY" pattern, drop firstname
    m = FIRSTNAME_LASTNAME_RE.match(s)
    if m:
        firstname, lastname, rest = m.group(1), m.group(2), m.group(3)
        # Don't drop if "firstname" is actually a recognizable lastname-only token (heuristic skip)
        common_first = {"Steve","Lauren","Jordan","Chris","Mark","Anna","Pete","Tony"}
        if firstname in common_first or len(firstname) < 8:
            s = f"{lastname} {rest}".strip()
    return s


def main() -> None:
    fixes: list[tuple[int, str, str, str | None, str]] = []  # (id, old, new_short, new_author, reason)

    with engine.connect() as conn:
        rows = conn.execute(sqltext("SELECT id, cite_short, author_last FROM sources ORDER BY id")).all()

    for sid, old_short, old_author in rows:
        if sid in MANUAL:
            new_short, new_author, _ = MANUAL[sid]
            if new_short != old_short or new_author != old_author:
                fixes.append((sid, old_short, new_short, new_author, "manual"))
            continue
        new_short = clean_cite_short(old_short)
        # Author cleanup: same logic
        new_author = clean_cite_short(old_author or "")
        # Strip trailing year token from author_last (auto-parser sometimes pulled "Smith YY" into author_last)
        new_author = re.sub(r"\s*['’]?\d{1,4}(?:[/\-]\d{1,2}(?:[/\-]\d{2,4})?)?$", "", new_author).strip()
        if new_short != old_short or new_author != old_author:
            fixes.append((sid, old_short, new_short, new_author, "auto"))

    print(f"--- {len(fixes)} sources to fix ---\n")
    for sid, old, new_s, new_a, why in fixes:
        print(f"  src {sid:3d} [{why}]  {old!r:35s} -> {new_s!r}  (author -> {new_a!r})")

    if not fixes:
        print("Nothing to do.")
        return

    with engine.begin() as conn:
        for sid, _old, new_s, new_a, _why in fixes:
            if new_a is not None:
                conn.execute(
                    sqltext("UPDATE sources SET cite_short = :s, author_last = :a WHERE id = :i"),
                    {"s": new_s, "a": new_a, "i": sid},
                )
            else:
                conn.execute(
                    sqltext("UPDATE sources SET cite_short = :s WHERE id = :i"),
                    {"s": new_s, "i": sid},
                )

    # Apply MANUAL author_full updates separately
    with engine.begin() as conn:
        for sid, (_short, _author, full) in MANUAL.items():
            if full is not None:
                conn.execute(
                    sqltext("UPDATE sources SET author_full = :f WHERE id = :i"),
                    {"f": full, "i": sid},
                )

    print(f"\n✓ Updated {len(fixes)} sources.")


if __name__ == "__main__":
    main()
