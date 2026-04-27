"""Parse opencaselist weekly-dump filenames into structured metadata.

Filenames roughly follow:

    {School}-{Team}-{Side}[-{SortPrefix}{Sep}]{Tournament}-{RoundInfo}.docx

But the wild has variants:
- ``MontgomeryBell-HoLi-Aff-10---TOC-Round-1.docx``  (triple-hyphen)
- ``MontgomeryBell-FuRo-Aff-12.-Tournament-of-Champions-Round-1.docx``  (.-)
- ``DallasHighlandPark-CoBr-Neg-14--The-Last-Dance-Round-7.docx``  (double-hyphen)
- ``Jenks-KoBa-Aff-3-East-Oklahoma-District-Tournament-Round-1.docx``  (single-hyphen)
- ``Westwood-AnGu-Neg-Tournament-of-Champions-Round-2.docx``  (no sort prefix)
- ``GreenhillSchool-AlMo-Neg-08---TFA-State-Finals.docx``  (named round)

The sort prefix appears to be a per-team ordinal across their disclosed
rounds; we capture it for ordering but don't otherwise use it. The
tournament name is reconstructed by replacing remaining hyphens with
spaces — there's no way to recover the original casing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WikiMeta:
    school: str
    team: str
    side: str  # "Aff" or "Neg"
    tournament: str | None
    round_name: str | None
    sort_prefix: str | None


_NAMED_ROUNDS = (
    "Finals",
    "Semifinals",
    "Semis",
    "Quarterfinals",
    "Quarters",
    "Octofinals",
    "Octos",
    "Doubles",
    "Triples",
    "Elims",
    "RR",
    "Round-Robin",
)
_NAMED_ROUNDS_RE = "|".join(_NAMED_ROUNDS)

# After {school}-{team}-{side}-, the rest may start with an optional
# sort prefix (digits, optionally followed by a period) and a separator
# of one to three hyphens. Then comes the tournament + round.
_REST_RE = re.compile(
    r"^(?:(?P<sort>\d{1,3})\.?(?:---|--|-))?(?P<body>.+)$"
)
_NUMERIC_ROUND_RE = re.compile(r"^(?P<tournament>.+?)-Round-(?P<n>\d+)$")
_NAMED_ROUND_RE = re.compile(
    rf"^(?P<tournament>.+?)-(?P<round>{_NAMED_ROUNDS_RE})$",
    re.IGNORECASE,
)


def parse_wiki_filename(name: str) -> WikiMeta | None:
    """Parse a filename into ``WikiMeta``, or ``None`` if it doesn't fit.

    Returns ``None`` for filenames that don't have the basic
    ``School-Team-Side-…`` shape — those are skipped at ingest time.
    """
    stem = name.rsplit(".docx", 1)[0] if name.lower().endswith(".docx") else name
    parts = stem.split("-", 3)
    if len(parts) < 4:
        return None
    school, team, side, rest = parts
    if side not in ("Aff", "Neg"):
        return None

    m = _REST_RE.match(rest)
    if m is None:
        # rest empty — keep school/team/side but no tournament info
        return WikiMeta(
            school=school, team=team, side=side,
            tournament=None, round_name=None, sort_prefix=None,
        )
    sort_prefix = m.group("sort")
    body = m.group("body")

    nm = _NUMERIC_ROUND_RE.match(body)
    if nm:
        tournament = nm.group("tournament").replace("-", " ").strip()
        round_name = f"Round {nm.group('n')}"
    else:
        nm2 = _NAMED_ROUND_RE.match(body)
        if nm2:
            tournament = nm2.group("tournament").replace("-", " ").strip()
            round_name = nm2.group("round")
        else:
            tournament = body.replace("-", " ").strip() or None
            round_name = None

    return WikiMeta(
        school=school,
        team=team,
        side=side,
        tournament=tournament or None,
        round_name=round_name,
        sort_prefix=sort_prefix,
    )
