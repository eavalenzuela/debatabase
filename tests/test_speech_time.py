"""Tests for the speech-time estimation helpers (speech_time.py)."""

from __future__ import annotations

from debatabase.speech_time import (
    DEFAULT_WPM,
    SpeechStats,
    format_seconds,
    highlight_stats,
    highlight_text,
)

TEXT = "Hegemony is resilient and decline is slow."


def test_highlight_text_joins_spans_in_order():
    markup = [
        {"start": 26, "end": 33, "kind": "highlight"},  # "decline"
        {"start": 0, "end": 8, "kind": "highlight"},    # "Hegemony"
        {"start": 0, "end": 21, "kind": "underline"},   # ignored
    ]
    assert highlight_text(TEXT, markup) == "Hegemony decline"


def test_highlight_text_ignores_underlines_and_empties():
    markup = [
        {"start": 0, "end": 21, "kind": "underline"},
        {"start": 5, "end": 5, "kind": "highlight"},
    ]
    assert highlight_text(TEXT, markup) == ""


def test_stats_zero_highlights_is_zero_seconds():
    assert highlight_stats(TEXT, []) == SpeechStats(words=0, seconds=0)


def test_stats_counts_words_and_rounds_seconds():
    # Whole text highlighted: 7 words at 270 wpm ≈ 1.6s → rounds to 2.
    markup = [{"start": 0, "end": len(TEXT), "kind": "highlight"}]
    stats = highlight_stats(TEXT, markup)
    assert stats.words == 7
    assert stats.seconds == 2


def test_stats_short_read_floors_at_one_second():
    markup = [{"start": 0, "end": 8, "kind": "highlight"}]  # one word
    assert highlight_stats(TEXT, markup).seconds == 1


def test_stats_wpm_scales():
    markup = [{"start": 0, "end": len(TEXT), "kind": "highlight"}]
    slow = highlight_stats(TEXT, markup, wpm=DEFAULT_WPM // 2)
    fast = highlight_stats(TEXT, markup, wpm=DEFAULT_WPM)
    assert slow.seconds > fast.seconds


def test_format_seconds():
    assert format_seconds(0) == "0:00"
    assert format_seconds(42) == "0:42"
    assert format_seconds(95) == "1:35"
    assert format_seconds(600) == "10:00"
    assert format_seconds(-5) == "0:00"
