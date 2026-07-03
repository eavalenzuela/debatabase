"""Speech-time estimation for highlighted card text.

Debaters read the *highlighted* subset of a card in-round, so the useful
number when assembling a speech doc is "how long does this card take to
read aloud?". The estimate is words-per-minute over the highlight-only
text — the same text ``web/render.py``'s highlight-only mode shows.

``DEFAULT_WPM`` is tuned for competitive policy delivery (spreading),
which is much faster than conversational speech (~150 wpm). 270 is a
deliberately conservative middle: novices read slower, fast varsity
debaters clear 350+. The number is a planning aid, not a stopwatch.

All functions here are pure — no DB, no I/O — mirroring ``markup_ops``.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WPM = 270


@dataclass(frozen=True)
class SpeechStats:
    """Word count + estimated read seconds for a card's highlighted text."""

    words: int
    seconds: int


def highlight_text(text: str, markup: list[dict]) -> str:
    """The highlight-only reading of a card: highlighted spans joined by spaces.

    Mirrors ``render_card(..., mode="highlight-only")`` minus the HTML —
    only ``kind == "highlight"`` spans count, in start order.
    """
    spans = sorted(
        (s for s in (markup or []) if s.get("kind") == "highlight"),
        key=lambda s: s["start"],
    )
    return " ".join(
        text[s["start"]:s["end"]] for s in spans if s["end"] > s["start"]
    )


def highlight_stats(
    text: str, markup: list[dict], wpm: int = DEFAULT_WPM
) -> SpeechStats:
    """Word count and estimated read time of the highlighted text.

    Zero highlights → ``SpeechStats(0, 0)`` (an un-highlighted card has no
    in-round read). Seconds are rounded to the nearest whole second, with
    a floor of 1 for any non-empty read so short cards never show "0s".
    """
    words = len(highlight_text(text, markup).split())
    if words == 0:
        return SpeechStats(words=0, seconds=0)
    seconds = max(1, round(words * 60 / wpm))
    return SpeechStats(words=words, seconds=seconds)


def format_seconds(total: int) -> str:
    """``95`` → ``"1:35"``; ``42`` → ``"0:42"``. Compact m:ss for badges."""
    minutes, seconds = divmod(max(0, int(total)), 60)
    return f"{minutes}:{seconds:02d}"
